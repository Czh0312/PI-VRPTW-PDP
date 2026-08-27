# -*- coding: utf-8 -*-
"""Compact globally exact MILP for homogeneous one-handoff containers.

The formulation uses existing E2 delivery or pickup locations as handoff
points.  The carrier may leave containers after its visit and the receiver may
collect them later; a precedence constraint links the two vehicle-specific
visit times.  Homogeneous containers are handed over in integer quantities.  A
vehicle may visit an event without serving its customer task only when it sends
or receives at least one container there.

Two arc commodities enforce the single-handoff rule without tracking container
origins:

``own``
    containers collected directly from customers and not handed over yet;
``relay``
    containers received from another vehicle.

Only ``own`` flow can be transferred.  Consequently a received container can
never be transferred a second time.  Delivery weight/volume and both container
commodities are carried directly on route arcs, avoiding load-state Big-M
equalities altogether.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import gurobipy as gp
from gurobipy import GRB

from models.model_5.exact_joint import _add_e1_constraints
from models.solver_utils import extract_routes_e1


TOL = 1e-7


@dataclass(frozen=True)
class CompactEvent:
    id: str
    sat_id: str
    phys: str
    kind: str
    weight: float
    volume: float
    tw: tuple[float, float]
    service: float
    boxes: int


def _status_name(status_code):
    names = {
        GRB.OPTIMAL: "OPTIMAL",
        GRB.INFEASIBLE: "INFEASIBLE",
        GRB.INF_OR_UNBD: "INF_OR_UNBD",
        GRB.UNBOUNDED: "UNBOUNDED",
        GRB.CUTOFF: "CUTOFF",
        GRB.ITERATION_LIMIT: "ITERATION_LIMIT",
        GRB.NODE_LIMIT: "NODE_LIMIT",
        GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.SOLUTION_LIMIT: "SOLUTION_LIMIT",
        GRB.INTERRUPTED: "INTERRUPTED",
        GRB.NUMERIC: "NUMERIC",
        GRB.SUBOPTIMAL: "SUBOPTIMAL",
        GRB.USER_OBJ_LIMIT: "USER_OBJ_LIMIT",
        GRB.WORK_LIMIT: "WORK_LIMIT",
        GRB.MEM_LIMIT: "MEM_LIMIT",
    }
    return names.get(status_code, str(status_code))


def _container_count(task, container_weight, container_volume):
    """Return the integer homogeneous-container count represented by a pickup."""
    ratios = []
    if container_weight > TOL:
        ratios.append(task.w / container_weight)
    elif task.w > TOL:
        raise ValueError("container_weight=0 但 pickup 重量为正，无法换算空箱数")
    if container_volume > TOL:
        ratios.append(task.v / container_volume)
    elif task.v > TOL:
        raise ValueError("container_volume=0 但 pickup 体积为正，无法换算空箱数")
    if not ratios:
        raise ValueError("空箱重量和体积不能同时为 0")
    count = int(round(ratios[0]))
    if count <= 0 or any(abs(value - count) > 1e-6 for value in ratios):
        raise ValueError(
            f"pickup {task.id} 不是同质空箱的整数倍: ratios={ratios}"
        )
    return count


def _safe_horizon(problem, e1_tasks, events):
    inf = float("inf")
    finite = [0.0]
    for task in e1_tasks:
        finite.extend(value for value in task.tw if value < inf)
    for event in events.values():
        finite.extend(value for value in event.tw if value < inf)
    physical = [problem.depot.id]
    physical.extend(s.id for s in problem.satellites)
    physical.extend(c.id for c in problem.customers)
    max_travel = max(
        (problem.travel_time(i, j) for i in physical for j in physical),
        default=0.0,
    )
    total_service = sum(
        getattr(node, "service_time", 0.0) for node in problem.nodes.values()
    )
    # Every task event is visited at most once by each route.  The multiplier
    # is deliberately based on all E1/E2 tasks, so infinite-window return and
    # E1 pickup times remain safely bounded.
    return (
        max(finite, default=0.0)
        + (len(e1_tasks) + len(events) + 4) * max_travel
        + total_service
        + 1.0
    )


def build_compact_model6_exact(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
        TFp=9999.0, strengthen=True):
    """Build the compact asynchronous event-location Model 6 MILP."""
    depot_id = problem.depot.id
    satellites = [s.id for s in problem.satellites]
    e1_task_map = {task.id: task for task in e1_tasks}
    e1_nodes = [task.id for task in e1_tasks]
    n1e = [depot_id] + e1_nodes
    e1_vehicles = list(range(problem.e1_vehicle_count))
    vehicles_by_sat = {
        sat_id: [(sat_id, idx) for idx in range(vehicles_per_satellite)]
        for sat_id in satellites
    }

    cw = float(problem.fleet.container_weight)
    cv = float(problem.fleet.container_volume)
    events = {}
    events_by_sat = {sat_id: [] for sat_id in satellites}
    for sat_id, tasks in e2_tasks_by_sat.items():
        for task in tasks:
            boxes = _container_count(task, cw, cv) if task.kind == "p" else 0
            event = CompactEvent(
                id=task.id,
                sat_id=sat_id,
                phys=task.cust,
                kind=task.kind,
                weight=float(task.w),
                volume=float(task.v),
                tw=task.tw,
                service=float(getattr(problem.nodes[task.cust], "service_time", 0.0)),
                boxes=boxes,
            )
            events[event.id] = event
            events_by_sat[sat_id].append(event.id)

    horizon = _safe_horizon(problem, e1_tasks, events)
    if TFp < 9000.0:
        horizon = max(horizon, float(TFp) + 1.0)
    inf = float("inf")

    def phys(node_id):
        if node_id == depot_id or node_id in satellites:
            return node_id
        if node_id in e1_task_map:
            return e1_task_map[node_id].cust
        return events[node_id].phys

    def service(node_id):
        if node_id == depot_id or node_id in satellites:
            # Raw satellite nodes are E2 route depots.  Their handling time is
            # already charged at the E1 satellite delivery/pickup task nodes.
            return 0.0
        if node_id in e1_task_map:
            return float(getattr(problem.nodes[phys(node_id)], "service_time", 0.0))
        return events[node_id].service

    all_ids = list(dict.fromkeys(n1e + satellites + list(events)))
    distance = {}
    travel = {}
    for i in all_ids:
        for j in all_ids:
            if i == j:
                distance[i, j] = 0.0
                travel[i, j] = 0.0
            else:
                distance[i, j] = problem.distance(phys(i), phys(j))
                travel[i, j] = problem.travel_time(phys(i), phys(j))

    def tw(node_id):
        if node_id == depot_id or node_id in satellites:
            return (0.0, inf)
        if node_id in e1_task_map:
            return e1_task_map[node_id].tw
        return events[node_id].tw

    def arc_m(i, j):
        latest_i = tw(i)[1]
        upper_i = horizon if latest_i == inf else min(horizon, latest_i)
        earliest_j = tw(j)[0]
        return max(0.0, upper_i + service(i) + travel[i, j] - earliest_j)

    def time_infeasible(i, j):
        earliest_i = tw(i)[0]
        latest_j = tw(j)[1]
        return (
            latest_j < inf
            and earliest_i + service(i) + travel[i, j] > latest_j + TOL
        )

    e1_infeasible_arcs = {
        (i, j) for i in n1e for j in e1_nodes
        if i != j and time_infeasible(i, j)
    }

    model = gp.Model("Model6_Compact_Homogeneous_Event_Relay")

    # E1 uses the already regression-tested strengthened Model 5 constraints.
    x = model.addVars(n1e, n1e, e1_vehicles, vtype=GRB.BINARY, name="x")
    a1 = model.addVars(n1e, e1_vehicles, lb=0.0, ub=horizon, name="A_e1")
    q1 = model.addVars(
        n1e, e1_vehicles, lb=0.0, ub=problem.e1_weight_cap, name="Q_e1"
    )
    vol1 = model.addVars(
        n1e, e1_vehicles, lb=0.0, ub=problem.e1_volume_cap, name="Vol_e1"
    )

    e1_ctx = {
        "depot_id": depot_id,
        "F": satellites,
        "K": e1_vehicles,
        "N1E": n1e,
        "e1_nodes": e1_nodes,
        "e1_task_map": e1_task_map,
        "time_map": travel,
        "service_time": service,
        "M_time": horizon + max(travel.values(), default=0.0),
        "strengthen": bool(strengthen),
        "time_horizon": horizon,
        "e1_infeasible_arcs": e1_infeasible_arcs,
        "arc_time_m": arc_m,
        "M_weight_e1": 2 * problem.e1_weight_cap + 1000.0,
        "M_vol_e1": 2 * problem.e1_volume_cap + 1000.0,
    }
    variables = {"x": x, "A1": a1, "Q1": q1, "V1": vol1}
    _add_e1_constraints(model, problem, variables, e1_ctx, e1_tasks)
    if TFp < 9000.0:
        for task in e1_tasks:
            if task.kind != "p":
                continue
            for k in e1_vehicles:
                e1_visit = gp.quicksum(
                    x[i, task.id, k] for i in n1e if i != task.id
                )
                model.addConstr(
                    a1[task.id, k] >= TFp - horizon * (1 - e1_visit),
                    f"E1_TFp_{task.id}_{k}",
                )

    # Compact E2 route, service, time and commodity-flow variables.
    z = {}
    visit = {}
    serve_task = {}
    goods_w = {}
    goods_v = {}
    own = {}
    relay = {}
    event_time = {}
    visit_time = {}
    start_time = {}
    return_time = {}
    used = {}
    transfer = {}
    transfer_active = {}

    raw_box_capacity = min(
        problem.e2_weight_cap / cw if cw > TOL else float("inf"),
        problem.e2_volume_cap / cv if cv > TOL else float("inf"),
    )
    if not math.isfinite(raw_box_capacity):
        raise ValueError("空箱重量和体积不能同时为 0")
    max_boxes = int(math.floor(raw_box_capacity + TOL))
    if max_boxes <= 0:
        raise ValueError("E2 容量无法表示有效的空箱整数容量")

    for event_id in events:
        event = events[event_id]
        latest = horizon if event.tw[1] == inf else min(horizon, event.tw[1])
        event_time[event_id] = model.addVar(
            lb=max(0.0, event.tw[0]), ub=latest, name=f"T_{event_id}"
        )

    for sat_id in satellites:
        nodes = events_by_sat.get(sat_id, [])
        valid = [sat_id] + nodes
        for vehicle in vehicles_by_sat[sat_id]:
            idx = vehicle[1]
            used[vehicle] = model.addVar(vtype=GRB.BINARY, name=f"used_{sat_id}_{idx}")
            start_time[vehicle] = model.addVar(
                lb=0.0, ub=horizon, name=f"start_{sat_id}_{idx}"
            )
            return_time[vehicle] = model.addVar(
                lb=0.0, ub=horizon, name=f"return_{sat_id}_{idx}"
            )
            for node_id in nodes:
                visit[node_id, vehicle] = model.addVar(
                    vtype=GRB.BINARY, name=f"visit_{node_id}_{idx}"
                )
                serve_task[node_id, vehicle] = model.addVar(
                    vtype=GRB.BINARY, name=f"serve_{node_id}_{idx}"
                )
                visit_time[node_id, vehicle] = model.addVar(
                    lb=0.0, ub=horizon, name=f"visit_time_{node_id}_{idx}"
                )
            for i in valid:
                for j in valid:
                    if i == j:
                        continue
                    key = (i, j, vehicle)
                    z[key] = model.addVar(vtype=GRB.BINARY, name=f"z_{i}_{j}_{idx}")
                    goods_w[key] = model.addVar(
                        lb=0.0, ub=problem.e2_weight_cap, name=f"gw_{i}_{j}_{idx}"
                    )
                    goods_v[key] = model.addVar(
                        lb=0.0, ub=problem.e2_volume_cap, name=f"gv_{i}_{j}_{idx}"
                    )
                    own[key] = model.addVar(
                        lb=0.0, ub=max_boxes, name=f"own_{i}_{j}_{idx}"
                    )
                    relay[key] = model.addVar(
                        lb=0.0, ub=max_boxes, name=f"relay_{i}_{j}_{idx}"
                    )

        vehs = vehicles_by_sat[sat_id]
        for event_id in nodes:
            for sender in vehs:
                for receiver in vehs:
                    if sender == receiver:
                        continue
                    transfer[event_id, sender, receiver] = model.addVar(
                        vtype=GRB.INTEGER, lb=0, ub=max_boxes,
                        name=(f"handoff_{event_id}_{sender[1]}_{receiver[1]}")
                    )
                    transfer_active[event_id, sender, receiver] = model.addVar(
                        vtype=GRB.BINARY,
                        name=(f"handoff_used_{event_id}_{sender[1]}_"
                              f"{receiver[1]}")
                    )

    # E1 + E2 total travel distance.
    objective = gp.quicksum(
        distance[i, j] * x[i, j, k]
        for i in n1e for j in n1e for k in e1_vehicles if i != j
    ) + gp.quicksum(distance[i, j] * z[i, j, vehicle] for i, j, vehicle in z)
    model.setObjective(objective, GRB.MINIMIZE)

    for sat_id in satellites:
        nodes = events_by_sat.get(sat_id, [])
        valid = [sat_id] + nodes
        vehs = vehicles_by_sat[sat_id]
        if not nodes:
            for vehicle in vehs:
                model.addConstr(used[vehicle] == 0)
            continue

        def incoming(var_dict, node_id, vehicle):
            return gp.quicksum(
                var_dict[i, node_id, vehicle]
                for i in valid if i != node_id and (i, node_id, vehicle) in var_dict
            )

        def outgoing(var_dict, node_id, vehicle):
            return gp.quicksum(
                var_dict[node_id, j, vehicle]
                for j in valid if j != node_id and (node_id, j, vehicle) in var_dict
            )

        # Every customer task has one server.  Other visits must be justified
        # by a positive integer handoff at that event.
        for node_id in nodes:
            model.addConstr(
                gp.quicksum(serve_task[node_id, vehicle] for vehicle in vehs) == 1,
                f"TaskServeOnce_{node_id}",
            )
            for vehicle in vehs:
                sent = gp.quicksum(
                    transfer[node_id, vehicle, other]
                    for other in vehs if other != vehicle
                )
                received = gp.quicksum(
                    transfer[node_id, other, vehicle]
                    for other in vehs if other != vehicle
                )
                model.addConstr(
                    serve_task[node_id, vehicle] <= visit[node_id, vehicle],
                    f"ServeImpliesVisit_{node_id}_{vehicle[1]}",
                )
                model.addConstr(
                    visit[node_id, vehicle]
                    <= serve_task[node_id, vehicle] + sent + received,
                    f"VisitHasPurpose_{node_id}_{vehicle[1]}",
                )
                model.addConstr(
                    sent <= max_boxes * visit[node_id, vehicle],
                    f"SenderVisits_{node_id}_{vehicle[1]}",
                )
                model.addConstr(
                    received <= max_boxes * visit[node_id, vehicle],
                    f"ReceiverVisits_{node_id}_{vehicle[1]}",
                )

            for sender in vehs:
                for receiver in vehs:
                    if sender == receiver:
                        continue
                    quantity = transfer[node_id, sender, receiver]
                    active = transfer_active[node_id, sender, receiver]
                    model.addConstr(
                        quantity <= max_boxes * active,
                        f"HandoffUseUpper_{node_id}_{sender[1]}_{receiver[1]}",
                    )
                    model.addConstr(
                        quantity >= active,
                        f"HandoffUseLower_{node_id}_{sender[1]}_{receiver[1]}",
                    )
                    # The carrier leaves the boxes after completing its visit;
                    # temporary storage at the customer lets the receiver arrive
                    # later.  This is the asynchronous handoff used by Model 5.
                    model.addConstr(
                        visit_time[node_id, receiver]
                        >= visit_time[node_id, sender] + service(node_id)
                        - (horizon + service(node_id)) * (1 - active),
                        f"HandoffPrecedence_{node_id}_{sender[1]}_{receiver[1]}",
                    )

        for vehicle in vehs:
            idx = vehicle[1]
            depot_out = outgoing(z, sat_id, vehicle)
            depot_in = incoming(z, sat_id, vehicle)
            model.addConstr(depot_out == used[vehicle], f"RouteStart_{sat_id}_{idx}")
            model.addConstr(depot_in == used[vehicle], f"RouteEnd_{sat_id}_{idx}")
            model.addConstr(start_time[vehicle] <= horizon * used[vehicle])
            model.addConstr(return_time[vehicle] <= horizon * used[vehicle])
            model.addConstr(return_time[vehicle] >= start_time[vehicle])

            for node_id in nodes:
                model.addConstr(
                    incoming(z, node_id, vehicle) == visit[node_id, vehicle],
                    f"VisitIn_{node_id}_{idx}",
                )
                model.addConstr(
                    outgoing(z, node_id, vehicle) == visit[node_id, vehicle],
                    f"VisitOut_{node_id}_{idx}",
                )
                model.addConstr(
                    visit[node_id, vehicle] <= used[vehicle],
                    f"ConnectedVisit_{node_id}_{idx}",
                )
                model.addConstr(
                    visit_time[node_id, vehicle] <= horizon * visit[node_id, vehicle],
                    f"VisitTimeActive_{node_id}_{idx}",
                )
                model.addConstr(
                    visit_time[node_id, vehicle]
                    >= event_time[node_id] - horizon * (1 - serve_task[node_id, vehicle]),
                    f"ServiceTimeLinkLo_{node_id}_{idx}",
                )
                model.addConstr(
                    visit_time[node_id, vehicle]
                    <= event_time[node_id] + horizon * (1 - serve_task[node_id, vehicle]),
                    f"ServiceTimeLinkHi_{node_id}_{idx}",
                )

            # Commodity sources and sinks at the satellite.
            model.addConstr(
                outgoing(goods_w, sat_id, vehicle)
                == gp.quicksum(
                    events[n].weight * serve_task[n, vehicle]
                    for n in nodes if events[n].kind == "d"
                ),
                f"GoodsWSource_{sat_id}_{idx}",
            )
            model.addConstr(
                outgoing(goods_v, sat_id, vehicle)
                == gp.quicksum(
                    events[n].volume * serve_task[n, vehicle]
                    for n in nodes if events[n].kind == "d"
                ),
                f"GoodsVSource_{sat_id}_{idx}",
            )
            model.addConstr(outgoing(own, sat_id, vehicle) == 0)
            model.addConstr(outgoing(relay, sat_id, vehicle) == 0)
            model.addConstr(incoming(goods_w, sat_id, vehicle) == 0)
            model.addConstr(incoming(goods_v, sat_id, vehicle) == 0)

            for node_id in nodes:
                event = events[node_id]
                sent = gp.quicksum(
                    transfer[node_id, vehicle, other]
                    for other in vehs if other != vehicle
                )
                received = gp.quicksum(
                    transfer[node_id, other, vehicle]
                    for other in vehs if other != vehicle
                )
                delivery_w = event.weight if event.kind == "d" else 0.0
                delivery_v = event.volume if event.kind == "d" else 0.0
                pickup_boxes = event.boxes if event.kind == "p" else 0

                model.addConstr(
                    incoming(goods_w, node_id, vehicle)
                    - outgoing(goods_w, node_id, vehicle)
                    == delivery_w * serve_task[node_id, vehicle],
                    f"GoodsWBalance_{node_id}_{idx}",
                )
                model.addConstr(
                    incoming(goods_v, node_id, vehicle)
                    - outgoing(goods_v, node_id, vehicle)
                    == delivery_v * serve_task[node_id, vehicle],
                    f"GoodsVBalance_{node_id}_{idx}",
                )
                model.addConstr(
                    outgoing(own, node_id, vehicle)
                    - incoming(own, node_id, vehicle)
                    == pickup_boxes * serve_task[node_id, vehicle] - sent,
                    f"OwnBalance_{node_id}_{idx}",
                )
                # A genuine relay requires the sender to have transported the
                # container into the handoff visit.  Without this inequality a
                # vehicle could "collect" and immediately leave the same box at
                # its origin, creating a zero-leg storage loophole.
                model.addConstr(
                    sent <= incoming(own, node_id, vehicle),
                    f"TransferOnlyCarriedOwn_{node_id}_{idx}",
                )
                model.addConstr(
                    outgoing(relay, node_id, vehicle)
                    - incoming(relay, node_id, vehicle)
                    == received,
                    f"RelayBalance_{node_id}_{idx}",
                )

            for i in valid:
                for j in valid:
                    key = (i, j, vehicle)
                    if key not in z:
                        continue
                    model.addConstr(
                        goods_w[key] + cw * (own[key] + relay[key])
                        <= problem.e2_weight_cap * z[key],
                        f"ArcWeightCap_{sat_id}_{idx}_{i}_{j}",
                    )
                    model.addConstr(
                        goods_v[key] + cv * (own[key] + relay[key])
                        <= problem.e2_volume_cap * z[key],
                        f"ArcVolumeCap_{sat_id}_{idx}_{i}_{j}",
                    )

                    if i == sat_id:
                        model.addConstr(
                            visit_time[j, vehicle]
                            >= start_time[vehicle] + travel[i, j]
                            - (horizon + travel[i, j]) * (1 - z[key]),
                            f"TimeStart_{sat_id}_{idx}_{j}",
                        )
                    elif j == sat_id:
                        m_ret = horizon + service(i) + travel[i, j]
                        model.addConstr(
                            return_time[vehicle] >= visit_time[i, vehicle] + service(i)
                            + travel[i, j] - m_ret * (1 - z[key]),
                            f"ReturnLo_{sat_id}_{idx}_{i}",
                        )
                        model.addConstr(
                            return_time[vehicle] <= visit_time[i, vehicle] + service(i)
                            + travel[i, j] + m_ret * (1 - z[key]),
                            f"ReturnHi_{sat_id}_{idx}_{i}",
                        )
                    else:
                        m_arc = horizon + service(i) + travel[i, j]
                        model.addConstr(
                            visit_time[j, vehicle]
                            >= visit_time[i, vehicle] + service(i)
                            + travel[i, j] - m_arc * (1 - z[key]),
                            f"EventTime_{sat_id}_{idx}_{i}_{j}",
                        )

        # Homogeneous E2 vehicle label symmetry.
        if strengthen:
            rank = {node_id: pos for pos, node_id in enumerate(sorted(nodes), 1)}
            for idx in range(len(vehs) - 1):
                left, right = vehs[idx], vehs[idx + 1]
                model.addConstr(used[left] >= used[right], f"VehicleSym_{sat_id}_{idx}")
                first_left = gp.quicksum(
                    rank[n] * z[sat_id, n, left]
                    for n in nodes if (sat_id, n, left) in z
                )
                first_right = gp.quicksum(
                    rank[n] * z[sat_id, n, right]
                    for n in nodes if (sat_id, n, right) in z
                )
                model.addConstr(
                    first_left <= first_right + len(nodes) * (1 - used[right]),
                    f"FirstEventSym_{sat_id}_{idx}",
                )

        # Delivery precedes pickup at each original customer.
        by_phys = {}
        for node_id in nodes:
            by_phys.setdefault(events[node_id].phys, {})[events[node_id].kind] = node_id
        for physical, pair in by_phys.items():
            if "d" in pair and "p" in pair:
                model.addConstr(
                    event_time[pair["p"]] >= event_time[pair["d"]]
                    + service(pair["d"]),
                    f"DeliveryBeforePickup_{physical}",
                )

        # E1 delivery releases E2 routes; E1 pickup waits for every used route.
        e1_delivery = f"{sat_id}d"
        e1_pickup = f"{sat_id}p"
        if e1_delivery in e1_task_map:
            for vehicle in vehs:
                for k in e1_vehicles:
                    e1_visit = gp.quicksum(
                        x[i, e1_delivery, k] for i in n1e if i != e1_delivery
                    )
                    model.addConstr(
                        start_time[vehicle] >= a1[e1_delivery, k]
                        + service(e1_delivery)
                        - horizon * (2 - e1_visit - used[vehicle]),
                        f"Release_{sat_id}_{vehicle[1]}_{k}",
                    )
        if e1_pickup in e1_task_map:
            for vehicle in vehs:
                for k in e1_vehicles:
                    e1_visit = gp.quicksum(
                        x[i, e1_pickup, k] for i in n1e if i != e1_pickup
                    )
                    model.addConstr(
                        a1[e1_pickup, k] >= return_time[vehicle]
                        - horizon * (2 - e1_visit - used[vehicle]),
                        f"CollectAfterReturn_{sat_id}_{vehicle[1]}_{k}",
                    )

    variables.update({
        "z": z,
        "visit": visit,
        "serve": serve_task,
        "goods_w": goods_w,
        "goods_v": goods_v,
        "own": own,
        "relay": relay,
        "transfer": transfer,
        "transfer_active": transfer_active,
        "event_time": event_time,
        "visit_time": visit_time,
        "start_time": start_time,
        "return_time": return_time,
        "used": used,
    })
    ctx = {
        **e1_ctx,
        "events": events,
        "events_by_sat": events_by_sat,
        "V_by_sat": vehicles_by_sat,
        "dist_map": distance,
        "max_boxes": max_boxes,
        "container_weight": cw,
        "container_volume": cv,
        "formulation": "compact_homogeneous_async_event_arc_flow",
        "proof_scope": "compact_async_event_handoff_model6",
    }
    model.update()
    return model, variables, ctx


def apply_no_transfer_warm_start(variables, ctx, routes):
    """Seed the compact model with ordinary Model 3 route lists."""
    if not routes:
        return
    e1_routes, e2_routes = routes
    for group in (
        "x", "z", "visit", "serve", "used", "transfer", "transfer_active"
    ):
        for var in variables[group].values():
            var.Start = 0.0

    def delivery_load(route, task_map):
        total = 0.0
        for node in route:
            if node not in task_map or task_map[node].kind != "d":
                continue
            task = task_map[node]
            total += float(
                task.w if hasattr(task, "w") else task.weight
            )
        return total

    e1_map = ctx["e1_task_map"]
    ordered_e1 = sorted(e1_routes or [], key=lambda r: delivery_load(r, e1_map), reverse=True)
    for k, route in zip(ctx["K"], ordered_e1):
        for i, j in zip(route, route[1:]):
            if (i, j, k) in variables["x"]:
                variables["x"][i, j, k].Start = 1.0

    events = ctx["events"]
    for sat_id, sat_routes in (e2_routes or {}).items():
        vehs = ctx["V_by_sat"].get(sat_id, [])
        event_rank = {
            node_id: pos
            for pos, node_id in enumerate(
                sorted(ctx["events_by_sat"].get(sat_id, [])), 1
            )
        }

        def first_event_rank(route):
            for node in route:
                if node in event_rank:
                    return event_rank[node]
            return len(event_rank) + 1

        # Match the exact model's first-event vehicle-label symmetry.
        ordered = sorted(
            sat_routes,
            key=lambda r: (first_event_rank(r), -delivery_load(r, events)),
        )
        for vehicle, route in zip(vehs, ordered):
            clean = [node for node in route if node == sat_id or node in events]
            if not clean or clean[0] != sat_id:
                clean.insert(0, sat_id)
            if clean[-1] != sat_id:
                clean.append(sat_id)
            valid = True
            for i, j in zip(clean, clean[1:]):
                if (i, j, vehicle) not in variables["z"]:
                    valid = False
                    break
            if not valid:
                continue
            variables["used"][vehicle].Start = 1.0
            for i, j in zip(clean, clean[1:]):
                variables["z"][i, j, vehicle].Start = 1.0
            for node in clean[1:-1]:
                variables["visit"][node, vehicle].Start = 1.0
                variables["serve"][node, vehicle].Start = 1.0


def complete_no_transfer_warm_start(model, variables, time_limit=10.0):
    """Complete a route-only start by solving its fixed continuous subproblem.

    Gurobi may otherwise abandon a partial route start before reconstructing
    all E1/E2 times and four arc commodities.  The copied model fixes every
    discrete decision to the supplied no-transfer route, solves the remaining
    LP, and copies the complete primal vector back as the original MIP start.
    """
    model.update()
    fixed = model.copy()
    fixed.setParam("OutputFlag", 0)
    fixed.setParam("TimeLimit", float(time_limit))
    fixed_by_name = {var.VarName: var for var in fixed.getVars()}
    discrete_groups = (
        "x", "z", "visit", "serve", "used", "transfer", "transfer_active"
    )
    for group in discrete_groups:
        for original in variables[group].values():
            target = fixed_by_name[original.VarName]
            value = float(round(original.Start))
            target.LB = value
            target.UB = value
    fixed.optimize()
    if fixed.SolCount <= 0:
        return False
    for original in model.getVars():
        original.Start = fixed_by_name[original.VarName].X
    return True


def _follow_route(z_values, sat_id, nodes, vehicle):
    route = [sat_id]
    current = sat_id
    seen = set()
    for _ in range(len(nodes) + 2):
        candidates = [
            j for j in [sat_id] + nodes
            if j != current and z_values.get((current, j, vehicle), 0.0) > 0.5
        ]
        if not candidates:
            break
        nxt = candidates[0]
        route.append(nxt)
        if nxt == sat_id:
            break
        if nxt in seen:
            break
        seen.add(nxt)
        current = nxt
    return route


def extract_compact_solution(model, variables, ctx):
    x_values = model.getAttr("X", variables["x"])
    e1_with_vehicle = extract_routes_e1(
        x_values, ctx["depot_id"], ctx["e1_nodes"], ctx["K"]
    )
    e1_routes = [route for _vehicle, route in e1_with_vehicle]

    z_values = {key: var.X for key, var in variables["z"].items()}
    e2_routes = {sat_id: [] for sat_id in ctx["F"]}
    route_vehicles = {sat_id: [] for sat_id in ctx["F"]}
    for sat_id in ctx["F"]:
        nodes = ctx["events_by_sat"].get(sat_id, [])
        for vehicle in ctx["V_by_sat"][sat_id]:
            if variables["used"][vehicle].X <= 0.5:
                continue
            raw = _follow_route(z_values, sat_id, nodes, vehicle)
            display = [sat_id]
            for node in raw[1:-1]:
                if variables["serve"][node, vehicle].X > 0.5:
                    display.append(node)
                else:
                    display.append(f"MEET@{node}")
            display.append(sat_id)
            e2_routes[sat_id].append(display)
            route_vehicles[sat_id].append(vehicle[1])

    transshipments = []
    total_boxes = 0
    for (event_id, sender, receiver), var in variables["transfer"].items():
        quantity = int(round(var.X))
        if quantity <= 0:
            continue
        total_boxes += quantity
        event = ctx["events"][event_id]
        sender_arrival = float(variables["visit_time"][event_id, sender].X)
        receiver_arrival = float(variables["visit_time"][event_id, receiver].X)
        transshipments.append({
            "satellite": event.sat_id,
            "event": event_id,
            "physical_location": event.phys,
            "sender_vehicle": sender[1],
            "receiver_vehicle": receiver[1],
            "quantity": quantity,
            "task_service_time": float(variables["event_time"][event_id].X),
            "sender_arrival_time": sender_arrival,
            "handoff_ready_time": sender_arrival + float(event.service),
            "receiver_pickup_time": receiver_arrival,
        })
    return e1_routes, e2_routes, route_vehicles, transshipments, total_boxes


def model_statistics(model, variables, ctx):
    model.update()
    return {
        "variables": int(model.NumVars),
        "binary_variables": int(model.NumBinVars),
        "integer_variables": int(model.NumIntVars),
        "general_integer_variables": int(model.NumIntVars - model.NumBinVars),
        "constraints": int(model.NumConstrs),
        "nonzeros": int(model.NumNZs),
        "e2_route_arcs": len(variables["z"]),
        "handoff_quantity_variables": len(variables["transfer"]),
        "handoff_activation_variables": len(variables["transfer_active"]),
        "events": len(ctx["events"]),
    }
