# -*- coding: utf-8 -*-
"""Exact elementary E2-route pricing oracle.

The oracle optimizes one capacity- and time-window-feasible E2 route under
arbitrary arc costs and task rewards.  It accepts Ryan-Foster same/different
rules, so the same component can be used at a branch node.  It is deliberately
only a route oracle: a caller must supply dual-derived costs from a correct
joint master before its result can be used in a branch-price proof.
"""
import time

import gurobipy as gp
from gurobipy import GRB

from .route_universe import E2Route


EPS = 1e-6


def _service_time(problem, task):
    return getattr(problem.nodes[task.cust], "service_time", 0.0)


def _arc_cost(arc_costs, i, j, default):
    return float((arc_costs or {}).get((i, j), default))


def solve_e2_route_pricing(problem, sat_id, tasks, *, arc_costs=None,
                           task_rewards=None, same_pairs=None,
                           different_pairs=None, time_limit=60.0):
    """Return the minimum reduced-cost elementary route for one satellite.

    ``arc_costs`` may be distance, a master dual expression coefficient, or
    any other additive pricing coefficient.  ``task_rewards`` is subtracted
    once for each selected task.  An OPTIMAL status proves the returned bound
    for this *single-route* problem only.
    """
    started = time.time()
    task_map = {task.id: task for task in tasks}
    task_ids = list(task_map)
    nodes = [sat_id] + task_ids
    if not task_ids:
        return {
            "status": "OPTIMAL", "route": None, "objective": 0.0,
            "best_bound": 0.0, "runtime": 0.0,
        }

    finite_latest = [task.tw[1] for task in tasks
                     if task.tw[1] < float("inf")]
    horizon = max(100000.0, max(finite_latest + [0.0]) + 10000.0)
    m = gp.Model(f"E2_RoutePricing_{sat_id}")
    m.setParam("OutputFlag", 0)
    m.setParam("TimeLimit", time_limit)
    m.setParam("MIPGap", 0.0)

    chosen = m.addVars(task_ids, vtype=GRB.BINARY, name="chosen")
    arc = m.addVars([(i, j) for i in nodes for j in nodes if i != j],
                    vtype=GRB.BINARY, name="arc")
    order = m.addVars(task_ids, lb=0.0, ub=len(task_ids), name="order")
    arrival = m.addVars(task_ids, lb=0.0, ub=horizon, name="arrival")
    load_w = m.addVars(nodes, lb=0.0, ub=problem.e2_weight_cap, name="load_w")
    load_v = m.addVars(nodes, lb=0.0, ub=problem.e2_volume_cap, name="load_v")

    m.addConstr(gp.quicksum(arc[sat_id, node] for node in task_ids) == 1,
                name="start_once")
    m.addConstr(gp.quicksum(arc[node, sat_id] for node in task_ids) == 1,
                name="return_once")
    for node in task_ids:
        incoming = gp.quicksum(arc[i, node] for i in nodes if i != node)
        outgoing = gp.quicksum(arc[node, j] for j in nodes if j != node)
        m.addConstr(incoming == chosen[node], name=f"in_{node}")
        m.addConstr(outgoing == chosen[node], name=f"out_{node}")
        m.addConstr(order[node] >= chosen[node], name=f"order_lo_{node}")
        m.addConstr(order[node] <= len(task_ids) * chosen[node],
                    name=f"order_used_{node}")
        earliest, latest = task_map[node].tw
        m.addConstr(arrival[node] >= earliest * chosen[node],
                    name=f"tw_lo_{node}")
        if latest < float("inf"):
            m.addConstr(arrival[node] <= latest * chosen[node],
                        name=f"tw_hi_{node}")
        m.addConstr(load_w[node] <= problem.e2_weight_cap * chosen[node])
        m.addConstr(load_v[node] <= problem.e2_volume_cap * chosen[node])

    for i in task_ids:
        for j in task_ids:
            if i != j:
                m.addConstr(order[j] >= order[i] + 1
                            - len(task_ids) * (1 - arc[i, j]),
                            name=f"mtz_{i}_{j}")

    delivery_tasks = [task for task in tasks if task.kind == "d"]
    m.addConstr(load_w[sat_id] == gp.quicksum(
        task.w * chosen[task.id] for task in delivery_tasks
    ), name="initial_weight")
    m.addConstr(load_v[sat_id] == gp.quicksum(
        task.v * chosen[task.id] for task in delivery_tasks
    ), name="initial_volume")

    for i in nodes:
        predecessor_time = 0.0 if i == sat_id else arrival[i]
        predecessor_service = 0.0 if i == sat_id else _service_time(problem, task_map[i])
        predecessor_phys = sat_id if i == sat_id else task_map[i].cust
        for j in task_ids:
            if i == j:
                continue
            task_j = task_map[j]
            travel = problem.travel_time(predecessor_phys, task_j.cust)
            m.addConstr(arrival[j] >= predecessor_time + predecessor_service + travel
                        - horizon * (1 - arc[i, j]), name=f"time_{i}_{j}")
            signed_w = task_j.w if task_j.kind == "d" else -task_j.w
            signed_v = task_j.v if task_j.kind == "d" else -task_j.v
            m.addConstr(load_w[j] <= load_w[i] - signed_w
                        + (problem.e2_weight_cap + abs(signed_w)) * (1 - arc[i, j]))
            m.addConstr(load_w[j] >= load_w[i] - signed_w
                        - (problem.e2_weight_cap + abs(signed_w)) * (1 - arc[i, j]))
            m.addConstr(load_v[j] <= load_v[i] - signed_v
                        + (problem.e2_volume_cap + abs(signed_v)) * (1 - arc[i, j]))
            m.addConstr(load_v[j] >= load_v[i] - signed_v
                        - (problem.e2_volume_cap + abs(signed_v)) * (1 - arc[i, j]))

    for customer in {task.cust for task in tasks}:
        delivery_id, pickup_id = f"{customer}d", f"{customer}p"
        if delivery_id in task_map and pickup_id in task_map:
            m.addConstr(order[pickup_id] >= order[delivery_id] + 1
                        - len(task_ids) * (2 - chosen[delivery_id]
                                           - chosen[pickup_id]))
            m.addConstr(arc[pickup_id, delivery_id] == 0)

    for left, right in same_pairs or []:
        if left in chosen and right in chosen:
            m.addConstr(chosen[left] == chosen[right],
                        name=f"rf_same_{left}_{right}")
    for left, right in different_pairs or []:
        if left in chosen and right in chosen:
            m.addConstr(chosen[left] + chosen[right] <= 1,
                        name=f"rf_different_{left}_{right}")

    objective = gp.quicksum(
        _arc_cost(
            arc_costs, i, j,
            problem.distance(
                sat_id if i == sat_id else task_map[i].cust,
                sat_id if j == sat_id else task_map[j].cust,
            ),
        ) * arc[i, j] for i, j in arc.keys()
    ) - gp.quicksum((task_rewards or {}).get(node, 0.0) * chosen[node]
                    for node in task_ids)
    m.setObjective(objective, GRB.MINIMIZE)
    m.optimize()

    status = {
        GRB.OPTIMAL: "OPTIMAL", GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.INFEASIBLE: "INFEASIBLE",
    }.get(m.status, str(m.status))
    result = {
        "status": status,
        "objective": float(m.ObjVal) if m.SolCount else None,
        "best_bound": (float(m.ObjBound) if m.status != GRB.INFEASIBLE
                       else float("inf")),
        "runtime": time.time() - started,
        "route": None,
    }
    if not m.SolCount:
        return result

    path = [sat_id]
    current = sat_id
    while True:
        successor = next(
            (node for node in nodes if node != current
             and (current, node) in arc and arc[current, node].X > 0.5),
            None,
        )
        if successor is None:
            break
        path.append(successor)
        if successor == sat_id:
            break
        current = successor
    route_tasks = tuple(node for node in path if node in task_map)
    if len(path) >= 3 and path[-1] == sat_id:
        result["route"] = E2Route(sat_id, tuple(path), route_tasks)
    return result
