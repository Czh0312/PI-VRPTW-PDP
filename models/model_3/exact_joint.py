# models/model_3/exact_joint.py
# -*- coding: utf-8 -*-
"""
Joint exact MIP for Model 3.

This model optimizes routes and transshipment decisions together. For each E2
pickup task p, the model chooses one physical pickup location among customers
assigned to the same satellite. If that location differs from p's original
customer, it also creates a transport pickup/drop pair that must be served by a
different E2 vehicle before the relocated pickup is collected.

The formulation is intentionally explicit and is meant for small instances.
Candidate counts grow quickly with the number of customers.
"""
from dataclasses import dataclass

import gurobipy as gp
from gurobipy import GRB


@dataclass(frozen=True)
class E2NodeInfo:
    id: str
    sat_id: str
    kind: str
    phys: str
    w: float
    v: float
    tw: tuple
    original_task: str = None
    original_phys: str = None
    relocated_to: str = None


def build_model3_joint_exact(problem, e1_tasks, e2_tasks_by_sat,
                             vehicles_per_satellite,
                             max_relocation_candidates=1):
    depot_id = problem.depot.id
    F = [s.id for s in problem.satellites]
    K = list(range(problem.e1_vehicle_count))
    V_by_sat = {
        sid: [(sid, v) for v in range(vehicles_per_satellite)]
        for sid in F
    }

    e1_task_map = {t.id: t for t in e1_tasks}
    e1_nodes = [t.id for t in e1_tasks]
    N1E = [depot_id] + e1_nodes

    e2_nodes_by_sat, e2_info, pickup_candidate_nodes = _build_e2_nodes(
        problem, e2_tasks_by_sat, max_relocation_candidates
    )

    all_e2_nodes = [
        nid for nodes in e2_nodes_by_sat.values() for nid in nodes
    ]

    all_ids = list(set([depot_id] + F + e1_nodes + all_e2_nodes))

    def phys(node_id):
        if node_id == depot_id or node_id in F:
            return node_id
        if node_id in e1_task_map:
            return e1_task_map[node_id].cust
        if node_id in e2_info:
            return e2_info[node_id].phys
        return node_id

    def service_time(node_id):
        p = phys(node_id)
        node = problem.nodes.get(p)
        return getattr(node, "service_time", 0.0) if node else 0.0

    dist_map, time_map = {}, {}
    for i in all_ids:
        for j in all_ids:
            if i == j:
                dist_map[i, j] = 0.0
                time_map[i, j] = 0.0
            else:
                dist_map[i, j] = problem.distance(phys(i), phys(j))
                time_map[i, j] = problem.travel_time(phys(i), phys(j))

    m = gp.Model("2E_VRP_PDP_TR_Joint_Exact")

    # ---------------- E1 variables ----------------
    x = m.addVars(N1E, N1E, K, vtype=GRB.BINARY, name="x")
    A1 = m.addVars(N1E, K, lb=0.0, name="A_e1")
    Q1 = m.addVars(
        N1E, K, lb=0.0, ub=problem.e1_weight_cap, name="Q_e1"
    )
    V1 = m.addVars(
        N1E, K, lb=0.0, ub=problem.e1_volume_cap, name="Vol_e1"
    )

    # ---------------- E2 variables ----------------
    z, A2, Q2, V2 = {}, {}, {}, {}
    for sat_id in F:
        valid_nodes = [sat_id] + e2_nodes_by_sat.get(sat_id, [])
        for veh in V_by_sat[sat_id]:
            for i in valid_nodes:
                A2[i, veh] = m.addVar(lb=0.0, name=f"A2_{i}_{veh}")
                Q2[i, veh] = m.addVar(
                    lb=0.0, ub=problem.e2_weight_cap, name=f"Q2_{i}_{veh}"
                )
                V2[i, veh] = m.addVar(
                    lb=0.0, ub=problem.e2_volume_cap, name=f"V2_{i}_{veh}"
                )
                for j in valid_nodes:
                    if i != j:
                        z[i, j, veh] = m.addVar(
                            vtype=GRB.BINARY, name=f"z_{i}_{j}_{veh}"
                        )

    # Pickup-location selection variables.
    y = {}
    for pick_task, cand_nodes in pickup_candidate_nodes.items():
        for node_id in cand_nodes:
            y[node_id] = m.addVar(vtype=GRB.BINARY, name=f"y_{node_id}")

    A_start = m.addVars(
        F, range(vehicles_per_satellite), lb=0.0, name="A_start"
    )
    A_return = m.addVars(
        F, range(vehicles_per_satellite), lb=0.0, name="A_return"
    )

    ctx = {
        "depot_id": depot_id,
        "F": F,
        "K": K,
        "V_by_sat": V_by_sat,
        "vehicles_per_satellite": vehicles_per_satellite,
        "N1E": N1E,
        "e1_nodes": e1_nodes,
        "e1_task_map": e1_task_map,
        "e2_nodes_by_sat": e2_nodes_by_sat,
        "e2_info": e2_info,
        "pickup_candidate_nodes": pickup_candidate_nodes,
        "max_relocation_candidates": max_relocation_candidates,
        "dist_map": dist_map,
        "time_map": time_map,
        "service_time": service_time,
        "phys": phys,
        "M_time": 100000.0,
        "M_weight_e1": 2 * problem.e1_weight_cap + 1000.0,
        "M_weight_e2": 2 * problem.e2_weight_cap + 1000.0,
        "M_vol_e1": 2 * problem.e1_volume_cap + 1000.0,
        "M_vol_e2": 2 * problem.e2_volume_cap + 1000.0,
    }
    vars_dict = {
        "x": x,
        "A1": A1,
        "Q1": Q1,
        "V1": V1,
        "z": z,
        "A2": A2,
        "Q2": Q2,
        "V2": V2,
        "y": y,
        "A_start": A_start,
        "A_return": A_return,
    }

    _set_objective(m, vars_dict, ctx)
    _add_e1_constraints(m, problem, vars_dict, ctx, e1_tasks)
    _add_e2_constraints(m, problem, vars_dict, ctx, e2_tasks_by_sat)
    _add_linking_constraints(m, vars_dict, ctx, e2_tasks_by_sat)
    _apply_no_trans_warm_start(
        m, vars_dict, ctx, problem, e1_tasks, e2_tasks_by_sat,
        vehicles_per_satellite,
    )

    return m, vars_dict, ctx


def apply_route_warm_start(vars_dict, ctx, e1_routes, e2_routes_by_sat):
    """Use existing route lists as a no-transshipment MIP start."""
    for var in vars_dict["x"].values():
        var.Start = 0
    for var in vars_dict["z"].values():
        var.Start = 0
    for node_id, var in vars_dict["y"].items():
        info = ctx["e2_info"][node_id]
        var.Start = 1 if info.relocated_to == info.original_phys else 0

    for k, route in zip(ctx["K"], e1_routes or []):
        for i in range(len(route) - 1):
            key = (route[i], route[i + 1], k)
            if key in vars_dict["x"]:
                vars_dict["x"][key].Start = 1

    for sat_id, routes in (e2_routes_by_sat or {}).items():
        vehs = ctx["V_by_sat"].get(sat_id, [])
        for veh, route in zip(vehs, routes):
            mapped = [_warm_start_e2_node(n, ctx["e2_info"]) for n in route]
            for i in range(len(mapped) - 1):
                key = (mapped[i], mapped[i + 1], veh)
                if key in vars_dict["z"]:
                    vars_dict["z"][key].Start = 1


def _build_e2_nodes(problem, e2_tasks_by_sat, max_relocation_candidates):
    e2_nodes_by_sat = {}
    e2_info = {}
    pickup_candidate_nodes = {}

    for sat_id, tasks in e2_tasks_by_sat.items():
        nodes = []
        all_phys_candidates = sorted({t.cust for t in tasks})
        for task in tasks:
            if task.kind == "d":
                info = E2NodeInfo(
                    id=task.id, sat_id=sat_id, kind="d", phys=task.cust,
                    w=task.w, v=task.v, tw=task.tw,
                    original_task=task.id, original_phys=task.cust,
                    relocated_to=task.cust,
                )
                e2_info[task.id] = info
                nodes.append(task.id)
                continue

            cand_nodes = []
            phys_candidates = _candidate_phys_locations(
                problem, task.cust, all_phys_candidates,
                max_relocation_candidates,
            )
            for phys_id in phys_candidates:
                cand_id = f"{task.id}@{phys_id}"
                info = E2NodeInfo(
                    id=cand_id, sat_id=sat_id, kind="p", phys=phys_id,
                    w=task.w, v=task.v, tw=task.tw,
                    original_task=task.id, original_phys=task.cust,
                    relocated_to=phys_id,
                )
                e2_info[cand_id] = info
                nodes.append(cand_id)
                cand_nodes.append(cand_id)

                if phys_id != task.cust:
                    tp_id = f"TP@{task.id}@{phys_id}"
                    td_id = f"TD@{task.id}@{phys_id}"
                    e2_info[tp_id] = E2NodeInfo(
                        id=tp_id, sat_id=sat_id, kind="tp",
                        phys=task.cust, w=task.w, v=task.v,
                        tw=task.tw,
                        original_task=task.id, original_phys=task.cust,
                        relocated_to=phys_id,
                    )
                    e2_info[td_id] = E2NodeInfo(
                        id=td_id, sat_id=sat_id, kind="td",
                        phys=phys_id, w=task.w, v=task.v,
                        tw=(0.0, float("inf")),
                        original_task=task.id, original_phys=task.cust,
                        relocated_to=phys_id,
                    )
                    nodes.extend([tp_id, td_id])

            pickup_candidate_nodes[task.id] = cand_nodes

        e2_nodes_by_sat[sat_id] = nodes

    return e2_nodes_by_sat, e2_info, pickup_candidate_nodes


def _candidate_phys_locations(problem, original_phys, all_phys_candidates,
                              max_relocation_candidates):
    if max_relocation_candidates is None:
        others = [p for p in all_phys_candidates if p != original_phys]
    else:
        others = sorted(
            (p for p in all_phys_candidates if p != original_phys),
            key=lambda p: problem.distance(original_phys, p),
        )[:max(0, int(max_relocation_candidates))]
    return [original_phys] + others


def _set_objective(m, vars_dict, ctx):
    x = vars_dict["x"]
    z = vars_dict["z"]
    dist_map = ctx["dist_map"]
    N1E = ctx["N1E"]
    K = ctx["K"]

    obj_e1 = gp.quicksum(
        dist_map[i, j] * x[i, j, k]
        for i in N1E for j in N1E for k in K if i != j
    )
    obj_e2 = gp.quicksum(
        dist_map[i, j] * z[i, j, veh] for (i, j, veh) in z
    )
    m.setObjective(obj_e1 + obj_e2, GRB.MINIMIZE)


def _signed_load(info):
    if info.kind in ("d", "td"):
        return info.w, info.v
    return -info.w, -info.v


def _add_e1_constraints(m, problem, vars_dict, ctx, e1_tasks):
    x, A1, Q1, V1 = (
        vars_dict["x"], vars_dict["A1"], vars_dict["Q1"], vars_dict["V1"]
    )
    depot_id = ctx["depot_id"]
    N1E = ctx["N1E"]
    C1 = ctx["e1_nodes"]
    K = ctx["K"]
    e1_task_map = ctx["e1_task_map"]
    time_map = ctx["time_map"]
    service_time = ctx["service_time"]
    M_time = ctx["M_time"]

    for j in C1:
        m.addConstr(
            gp.quicksum(x[i, j, k] for i in N1E for k in K if i != j) == 1,
            f"E1_Visit_{j}",
        )

    for k in K:
        m.addConstr(gp.quicksum(x[depot_id, j, k] for j in C1) <= 1)
        m.addConstr(gp.quicksum(x[i, depot_id, k] for i in C1) <= 1)
        m.addConstr(A1[depot_id, k] == 0)

        depot_out = gp.quicksum(x[depot_id, j, k] for j in C1)
        for h in C1:
            m.addConstr(
                gp.quicksum(x[i, h, k] for i in N1E if i != h)
                == gp.quicksum(x[h, j, k] for j in N1E if j != h)
            )
            m.addConstr(
                gp.quicksum(x[i, h, k] for i in N1E if i != h) <= depot_out
            )

    m.addConstr(
        gp.quicksum(x[depot_id, j, k] for k in K for j in C1)
        == gp.quicksum(x[i, depot_id, k] for k in K for i in C1)
    )

    forbidden = set()
    for task in e1_tasks:
        if task.kind != "p":
            continue
        did = f"{task.cust}d"
        if did in e1_task_map:
            forbidden.add((task.id, did))
            for k in K:
                m.addConstr(x[task.id, did, k] == 0)

    for i in N1E:
        for j in C1:
            if i == j or (i, j) in forbidden:
                continue
            for k in K:
                m.addConstr(
                    A1[j, k] >= A1[i, k] + service_time(i) + time_map[i, j]
                    - M_time * (1 - x[i, j, k])
                )

    for j in C1:
        e_j, l_j = e1_task_map[j].tw
        for k in K:
            visits = gp.quicksum(x[i, j, k] for i in N1E if i != j)
            m.addConstr(A1[j, k] >= e_j - M_time * (1 - visits))
            if l_j < float("inf"):
                m.addConstr(A1[j, k] <= l_j + M_time * (1 - visits))

    total_del_w = sum(t.w for t in e1_tasks if t.kind == "d")
    total_del_v = sum(t.v for t in e1_tasks if t.kind == "d")
    m.addConstr(gp.quicksum(Q1[depot_id, k] for k in K) == total_del_w)
    m.addConstr(gp.quicksum(V1[depot_id, k] for k in K) == total_del_v)

    for k in K:
        used = gp.quicksum(x[depot_id, j, k] for j in C1)
        m.addConstr(Q1[depot_id, k] <= problem.e1_weight_cap * used)
        m.addConstr(V1[depot_id, k] <= problem.e1_volume_cap * used)

    for i in N1E:
        for j in C1:
            if i == j or (i, j) in forbidden:
                continue
            task = e1_task_map[j]
            q = task.w if task.kind == "d" else -task.w
            v = task.v if task.kind == "d" else -task.v
            for k in K:
                m.addConstr(
                    Q1[j, k] <= Q1[i, k] - q
                    + ctx["M_weight_e1"] * (1 - x[i, j, k])
                )
                m.addConstr(
                    Q1[j, k] >= Q1[i, k] - q
                    - ctx["M_weight_e1"] * (1 - x[i, j, k])
                )
                m.addConstr(
                    V1[j, k] <= V1[i, k] - v
                    + ctx["M_vol_e1"] * (1 - x[i, j, k])
                )
                m.addConstr(
                    V1[j, k] >= V1[i, k] - v
                    - ctx["M_vol_e1"] * (1 - x[i, j, k])
                )


def _incoming(z, node_id, veh, valid_nodes):
    return gp.quicksum(
        z[i, node_id, veh]
        for i in valid_nodes
        if i != node_id and (i, node_id, veh) in z
    )


def _add_e2_constraints(m, problem, vars_dict, ctx, e2_tasks_by_sat):
    z, A2, Q2, V2, y = (
        vars_dict["z"], vars_dict["A2"], vars_dict["Q2"],
        vars_dict["V2"], vars_dict["y"]
    )
    F = ctx["F"]
    V_by_sat = ctx["V_by_sat"]
    e2_nodes_by_sat = ctx["e2_nodes_by_sat"]
    e2_info = ctx["e2_info"]
    pickup_candidate_nodes = ctx["pickup_candidate_nodes"]
    time_map = ctx["time_map"]
    service_time = ctx["service_time"]
    M_time = ctx["M_time"]

    for pick_task, cand_nodes in pickup_candidate_nodes.items():
        m.addConstr(gp.quicksum(y[n] for n in cand_nodes) == 1)

    for sat_id in F:
        nodes = e2_nodes_by_sat.get(sat_id, [])
        valid_nodes = [sat_id] + nodes
        vehs = V_by_sat[sat_id]

        for veh in vehs:
            m.addConstr(A2[sat_id, veh] == 0)
            m.addConstr(
                gp.quicksum(z[sat_id, j, veh] for j in nodes
                            if (sat_id, j, veh) in z)
                == gp.quicksum(z[i, sat_id, veh] for i in nodes
                               if (i, sat_id, veh) in z)
            )
            m.addConstr(
                gp.quicksum(z[sat_id, j, veh] for j in nodes
                            if (sat_id, j, veh) in z) <= 1
            )

            for h in nodes:
                m.addConstr(
                    _incoming(z, h, veh, valid_nodes)
                    == gp.quicksum(z[h, j, veh] for j in valid_nodes
                                   if j != h and (h, j, veh) in z)
                )

        for node_id in nodes:
            info = e2_info[node_id]
            total_visit = gp.quicksum(
                _incoming(z, node_id, veh, valid_nodes) for veh in vehs
            )
            if info.kind == "d":
                m.addConstr(total_visit == 1)
            elif info.kind == "p":
                m.addConstr(total_visit == y[node_id])
            else:
                cand_id = f"{info.original_task}@{info.relocated_to}"
                m.addConstr(total_visit == y[cand_id])

        # Same carrier for transport pick/drop and not the beneficiary vehicle.
        for info in e2_info.values():
            if info.sat_id != sat_id or info.kind != "tp":
                continue
            cand_id = f"{info.original_task}@{info.relocated_to}"
            tp_id = info.id
            td_id = f"TD@{info.original_task}@{info.relocated_to}"
            for veh in vehs:
                u_tp = _incoming(z, tp_id, veh, valid_nodes)
                u_td = _incoming(z, td_id, veh, valid_nodes)
                u_pick = _incoming(z, cand_id, veh, valid_nodes)
                m.addConstr(u_tp == u_td)
                m.addConstr(u_tp + u_pick <= 1)
                m.addConstr(
                    A2[td_id, veh] >= A2[tp_id, veh]
                    + service_time(tp_id) + time_map[tp_id, td_id]
                    - M_time * (1 - u_tp)
                )

        for veh in vehs:
            del_nodes = [n for n in nodes if e2_info[n].kind == "d"]
            m.addConstr(
                Q2[sat_id, veh] == gp.quicksum(
                    e2_info[n].w * _incoming(z, n, veh, valid_nodes)
                    for n in del_nodes
                )
            )
            m.addConstr(
                V2[sat_id, veh] == gp.quicksum(
                    e2_info[n].v * _incoming(z, n, veh, valid_nodes)
                    for n in del_nodes
                )
            )

            for i in valid_nodes:
                for j in nodes:
                    if i == j or (i, j, veh) not in z:
                        continue
                    m.addConstr(
                        A2[j, veh] >= A2[i, veh] + service_time(i)
                        + time_map[i, j] - M_time * (1 - z[i, j, veh])
                    )
                    q, v = _signed_load(e2_info[j])
                    m.addConstr(
                        Q2[j, veh] <= Q2[i, veh] - q
                        + ctx["M_weight_e2"] * (1 - z[i, j, veh])
                    )
                    m.addConstr(
                        Q2[j, veh] >= Q2[i, veh] - q
                        - ctx["M_weight_e2"] * (1 - z[i, j, veh])
                    )
                    m.addConstr(
                        V2[j, veh] <= V2[i, veh] - v
                        + ctx["M_vol_e2"] * (1 - z[i, j, veh])
                    )
                    m.addConstr(
                        V2[j, veh] >= V2[i, veh] - v
                        - ctx["M_vol_e2"] * (1 - z[i, j, veh])
                    )


def _add_linking_constraints(m, vars_dict, ctx, e2_tasks_by_sat):
    x, z = vars_dict["x"], vars_dict["z"]
    A1, A2 = vars_dict["A1"], vars_dict["A2"]
    A_start, A_return = vars_dict["A_start"], vars_dict["A_return"]
    depot_id = ctx["depot_id"]
    F = ctx["F"]
    K = ctx["K"]
    N1E = ctx["N1E"]
    V_by_sat = ctx["V_by_sat"]
    e1_task_map = ctx["e1_task_map"]
    e2_nodes_by_sat = ctx["e2_nodes_by_sat"]
    e2_info = ctx["e2_info"]
    time_map = ctx["time_map"]
    service_time = ctx["service_time"]
    M_time = ctx["M_time"]

    for sat_id in F:
        e1_del = f"{sat_id}d"
        e1_pick = f"{sat_id}p"
        nodes = e2_nodes_by_sat.get(sat_id, [])
        if not nodes:
            continue

        valid_nodes = [sat_id] + nodes
        if e1_del in e1_task_map:
            s_f = service_time(e1_del)
            for v_idx, veh in enumerate(V_by_sat[sat_id]):
                for k in K:
                    e1_visits = gp.quicksum(
                        x[i, e1_del, k] for i in N1E if i != e1_del
                    )
                    m.addConstr(
                        A_start[sat_id, v_idx] >= A1[e1_del, k] + s_f
                        - M_time * (1 - e1_visits)
                    )

        for v_idx, veh in enumerate(V_by_sat[sat_id]):
            for node_id in nodes:
                info = e2_info[node_id]
                is_served = _incoming(z, node_id, veh, valid_nodes)
                abs_arrival = A_start[sat_id, v_idx] + A2[node_id, veh]

                if info.kind in ("d", "p", "tp"):
                    m.addConstr(
                        abs_arrival >= info.tw[0] - M_time * (1 - is_served)
                    )
                    if info.tw[1] < float("inf"):
                        m.addConstr(
                            abs_arrival <= info.tw[1]
                            + M_time * (1 - is_served)
                        )

            for i in nodes:
                if (i, sat_id, veh) not in z:
                    continue
                m.addConstr(
                    A_return[sat_id, v_idx]
                    >= A_start[sat_id, v_idx] + A2[i, veh]
                    + service_time(i) + time_map[i, sat_id]
                    - M_time * (1 - z[i, sat_id, veh])
                )
                m.addConstr(
                    A_return[sat_id, v_idx]
                    <= A_start[sat_id, v_idx] + A2[i, veh]
                    + service_time(i) + time_map[i, sat_id]
                    + M_time * (1 - z[i, sat_id, veh])
                )

        # Same original customer: delivery must precede pickup, even if served
        # by different E2 vehicles or if the pickup is relocated.
        for pick_info in e2_info.values():
            if pick_info.sat_id != sat_id or pick_info.kind != "p":
                continue
            del_id = f"{pick_info.original_phys}d"
            if del_id not in e2_info:
                continue
            service_d = service_time(del_id)
            for d_idx, d_veh in enumerate(V_by_sat[sat_id]):
                u_del = _incoming(z, del_id, d_veh, valid_nodes)
                abs_del = A_start[sat_id, d_idx] + A2[del_id, d_veh]
                for p_idx, p_veh in enumerate(V_by_sat[sat_id]):
                    u_pick = _incoming(z, pick_info.id, p_veh, valid_nodes)
                    abs_pick = A_start[sat_id, p_idx] + A2[pick_info.id, p_veh]
                    m.addConstr(
                        abs_pick >= abs_del + service_d
                        - M_time * (2 - u_del - u_pick)
                    )

        # A carrier can pick up a container for transshipment only after that
        # customer's delivery has been completed at the original location.
        for tp_info in e2_info.values():
            if tp_info.sat_id != sat_id or tp_info.kind != "tp":
                continue
            del_id = f"{tp_info.original_phys}d"
            if del_id not in e2_info:
                continue
            service_d = service_time(del_id)
            for d_idx, d_veh in enumerate(V_by_sat[sat_id]):
                u_del = _incoming(z, del_id, d_veh, valid_nodes)
                abs_del = A_start[sat_id, d_idx] + A2[del_id, d_veh]
                for tp_idx, tp_veh in enumerate(V_by_sat[sat_id]):
                    u_tp = _incoming(z, tp_info.id, tp_veh, valid_nodes)
                    abs_tp = A_start[sat_id, tp_idx] + A2[tp_info.id, tp_veh]
                    m.addConstr(
                        abs_tp >= abs_del + service_d
                        - M_time * (2 - u_del - u_tp)
                    )

        # Transport drop must happen before the relocated pickup is collected.
        for info in e2_info.values():
            if info.sat_id != sat_id or info.kind != "tp":
                continue
            cand_id = f"{info.original_task}@{info.relocated_to}"
            td_id = f"TD@{info.original_task}@{info.relocated_to}"
            for carrier_idx, carrier in enumerate(V_by_sat[sat_id]):
                u_drop = _incoming(z, td_id, carrier, valid_nodes)
                abs_drop = A_start[sat_id, carrier_idx] + A2[td_id, carrier]
                for ben_idx, ben in enumerate(V_by_sat[sat_id]):
                    u_pick = _incoming(z, cand_id, ben, valid_nodes)
                    abs_pick = A_start[sat_id, ben_idx] + A2[cand_id, ben]
                    m.addConstr(
                        abs_pick >= abs_drop
                        - M_time * (2 - u_drop - u_pick)
                    )

        # E1 pickup must wait until every used E2 route has returned to the
        # satellite, because the E1 pickup node aggregates all returned
        # containers for that satellite.
        if e1_pick in e1_task_map:
            for v_idx, _veh in enumerate(V_by_sat[sat_id]):
                for k in K:
                    e1_pick_visits = gp.quicksum(
                        x[i, e1_pick, k] for i in N1E if i != e1_pick
                    )
                    m.addConstr(
                        A1[e1_pick, k] >= A_return[sat_id, v_idx]
                        - M_time * (1 - e1_pick_visits)
                    )


def _apply_no_trans_warm_start(m, vars_dict, ctx, problem, e1_tasks,
                               e2_tasks_by_sat, vehicles_per_satellite):
    try:
        from models.model_3.alns_solver import Evaluator, build_initial_solution
    except Exception:
        return

    evaluator = Evaluator(
        problem, e1_tasks, e2_tasks_by_sat,
        vehicles_per_satellite=vehicles_per_satellite,
    )
    sol = build_initial_solution(problem, e1_tasks, e2_tasks_by_sat, evaluator)
    if not evaluator.is_feasible(sol):
        return

    x, z, y = vars_dict["x"], vars_dict["z"], vars_dict["y"]

    for var in x.values():
        var.Start = 0
    for var in z.values():
        var.Start = 0
    for node_id, var in y.items():
        info = ctx["e2_info"][node_id]
        var.Start = 1 if info.relocated_to == info.original_phys else 0

    depot_id = ctx["depot_id"]
    for k, route in zip(ctx["K"], sol.e1_routes):
        path = route.full_path()
        for i in range(len(path) - 1):
            key = (path[i], path[i + 1], k)
            if key in x:
                x[key].Start = 1

    for sat_id, routes in sol.e2_routes.items():
        vehs = ctx["V_by_sat"][sat_id]
        for veh, route in zip(vehs, routes):
            mapped = [sat_id]
            for node_id in route.nodes:
                mapped.append(_warm_start_e2_node(node_id, ctx["e2_info"]))
            mapped.append(sat_id)
            for i in range(len(mapped) - 1):
                key = (mapped[i], mapped[i + 1], veh)
                if key in z:
                    z[key].Start = 1


def _warm_start_e2_node(node_id, e2_info):
    if node_id in e2_info:
        return node_id
    if node_id.endswith("p"):
        original_phys = node_id[:-1]
        cand_id = f"{node_id}@{original_phys}"
        if cand_id in e2_info:
            return cand_id
    return node_id


def extract_solution(m, vars_dict, ctx, threshold=0.5):
    sol_x = m.getAttr("X", vars_dict["x"])
    sol_z = {k: v.X for k, v in vars_dict["z"].items()}
    depot_id = ctx["depot_id"]
    e1_nodes = ctx["e1_nodes"]
    K = ctx["K"]
    F = ctx["F"]

    e1_routes = []
    for k in K:
        cur = next(
            (j for j in e1_nodes if sol_x.get((depot_id, j, k), 0) > threshold),
            None,
        )
        if cur is None:
            continue
        route = [depot_id]
        seen = {depot_id}
        while cur and cur not in seen:
            route.append(cur)
            seen.add(cur)
            nxt = next(
                (j for j in [depot_id] + e1_nodes
                 if j != cur and sol_x.get((cur, j, k), 0) > threshold),
                None,
            )
            if nxt == depot_id or nxt is None:
                route.append(depot_id)
                break
            cur = nxt
        if len(route) > 2:
            e1_routes.append(route)

    e2_routes = {sat_id: [] for sat_id in F}
    for sat_id in F:
        nodes = ctx["e2_nodes_by_sat"].get(sat_id, [])
        for veh in ctx["V_by_sat"][sat_id]:
            cur = next(
                (j for j in nodes if sol_z.get((sat_id, j, veh), 0) > threshold),
                None,
            )
            if cur is None:
                continue
            route = [sat_id]
            seen = {sat_id}
            while cur and cur not in seen:
                route.append(_display_node(cur, ctx["e2_info"]))
                seen.add(cur)
                nxt = next(
                    (j for j in [sat_id] + nodes
                     if j != cur and sol_z.get((cur, j, veh), 0) > threshold),
                    None,
                )
                if nxt == sat_id or nxt is None:
                    route.append(sat_id)
                    break
                cur = nxt
            if len(route) > 2:
                e2_routes[sat_id].append(route)

    transshipments = []
    for node_id, var in vars_dict["y"].items():
        if var.X <= threshold:
            continue
        info = ctx["e2_info"][node_id]
        if info.relocated_to != info.original_phys:
            transshipments.append(
                (info.original_task, info.original_phys, info.relocated_to)
            )

    return e1_routes, e2_routes, transshipments


def _display_node(node_id, e2_info):
    info = e2_info[node_id]
    if info.kind == "d":
        return info.original_task
    if info.kind == "p":
        if info.relocated_to == info.original_phys:
            return info.original_task
        return info.relocated_to
    if info.kind == "tp":
        return f"TRANS_PICK_{info.original_phys}"
    if info.kind == "td":
        return f"TRANS_DROP_{info.relocated_to}"
    return node_id
