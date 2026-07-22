# -*- coding: utf-8 -*-
"""Vehicle/time-aware E2 route master for Model 2.

This is the correct restricted-master form for later branch-price work: E2
arcs are replaced by route columns, while each selected column has a departure
and return time and each customer has one absolute service time.  Thus E1/E2
handoff and cross-vehicle delivery-before-pickup constraints remain explicit.

With a complete route universe this is an exact reformulation; with a partial
pool it is intentionally reported only as a restricted master.
"""
import gurobipy as gp
from gurobipy import GRB

from models.solver_utils import extract_routes_e1

from .model import build_unified_pdp_model
from .route_universe import (
    E2Route,
    RouteUniverse,
    _route_capacity_feasible,
    _route_time_feasible,
    enumerate_e2_route_universe,
)
from .solver import _maybe_with_info, _solve_info


def _service_time(problem, task):
    return getattr(problem.nodes[task.cust], "service_time", 0.0)


def _add_route_master(m, problem, e2_tasks_by_sat, ctx, vars_dict, universe,
                      branch_rules=None):
    """Add E2 route columns and their joint timing/linking constraints."""
    M = ctx["M_time"]
    time_horizon = ctx["time_horizon"]
    x = vars_dict["x"]
    A_e1 = vars_dict["A_e1"]
    n1e = ctx["N1E"]
    k_set = ctx["K"]
    e1_task_map = ctx["e1_task_map"]

    task_map = {
        task.id: task
        for tasks in e2_tasks_by_sat.values()
        for task in tasks
    }
    route_keys = [
        (sat_id, rid)
        for sat_id, routes in universe.routes_by_sat.items()
        for rid in range(len(routes))
    ]
    y = m.addVars(route_keys, vtype=GRB.BINARY, name="route")
    route_start = m.addVars(route_keys, lb=0.0, ub=time_horizon,
                            name="route_start")
    route_return = m.addVars(route_keys, lb=0.0, ub=time_horizon,
                             name="route_return")
    task_time = m.addVars(list(task_map), lb=0.0, ub=time_horizon,
                           name="e2_task_time")

    e1_cost = gp.quicksum(
        ctx["dist_map"][i, j] * x[i, j, truck]
        for truck in k_set for i in n1e for j in n1e if i != j
    )
    e2_cost = gp.quicksum(
        sum(
            problem.distance(
                route.path[idx] if route.path[idx] == sat_id else task_map[route.path[idx]].cust,
                route.path[idx + 1] if route.path[idx + 1] == sat_id else task_map[route.path[idx + 1]].cust,
            )
            for idx in range(len(route.path) - 1)
        ) * y[sat_id, rid]
        for sat_id, routes in universe.routes_by_sat.items()
        for rid, route in enumerate(routes)
    )
    m.setObjective(e1_cost + e2_cost, GRB.MINIMIZE)

    for sat_id, tasks in e2_tasks_by_sat.items():
        routes = universe.routes_by_sat[sat_id]
        for task in tasks:
            m.addConstr(
                gp.quicksum(y[sat_id, rid] for rid, route in enumerate(routes)
                            if task.id in route.tasks) == 1,
                name=f"RouteCover_{sat_id}_{task.id}",
            )
            earliest, latest = task.tw
            m.addConstr(task_time[task.id] >= earliest,
                        name=f"RouteTWLo_{task.id}")
            if latest < float("inf"):
                m.addConstr(task_time[task.id] <= latest,
                            name=f"RouteTWHi_{task.id}")
        m.addConstr(
            gp.quicksum(y[sat_id, rid] for rid in range(len(routes)))
            <= ctx["vehicles_per_satellite"],
            name=f"RouteVehicleLimit_{sat_id}",
        )

        for rid, route in enumerate(routes):
            key = sat_id, rid
            m.addConstr(route_start[key] <= M * y[key],
                        name=f"RouteStartUsed_{sat_id}_{rid}")
            m.addConstr(route_return[key] <= M * y[key],
                        name=f"RouteReturnUsed_{sat_id}_{rid}")
            for pos, task_id in enumerate(route.tasks):
                task = task_map[task_id]
                predecessor = sat_id if pos == 0 else route.tasks[pos - 1]
                if predecessor == sat_id:
                    predecessor_time = route_start[key]
                    predecessor_service = 0.0
                    predecessor_phys = sat_id
                else:
                    predecessor_time = task_time[predecessor]
                    predecessor_service = _service_time(problem, task_map[predecessor])
                    predecessor_phys = task_map[predecessor].cust
                m.addConstr(
                    task_time[task_id] >= predecessor_time + predecessor_service
                    + problem.travel_time(predecessor_phys, task.cust)
                    - M * (1 - y[key]),
                    name=f"RouteTime_{sat_id}_{rid}_{pos}",
                )
            last_task = task_map[route.tasks[-1]]
            m.addConstr(
                route_return[key] >= task_time[last_task.id]
                + _service_time(problem, last_task)
                + problem.travel_time(last_task.cust, sat_id)
                - M * (1 - y[key]),
                name=f"RouteReturn_{sat_id}_{rid}",
            )

        for customer in sorted({task.cust for task in tasks}):
            delivery_id, pickup_id = f"{customer}d", f"{customer}p"
            if delivery_id in task_map and pickup_id in task_map:
                m.addConstr(
                    task_time[pickup_id] >= task_time[delivery_id]
                    + _service_time(problem, task_map[delivery_id]),
                    name=f"RouteCustomerPrecedence_{sat_id}_{customer}",
                )

        delivery_node, pickup_node = f"{sat_id}d", f"{sat_id}p"
        delivery_service = ctx["e1_service_time_map"].get(delivery_node, 0.0)
        for rid in range(len(routes)):
            key = sat_id, rid
            for truck in k_set:
                if delivery_node in e1_task_map:
                    delivery_visit = gp.quicksum(
                        x[i, delivery_node, truck]
                        for i in n1e if i != delivery_node
                    )
                    m.addConstr(
                        route_start[key] >= A_e1[delivery_node, truck]
                        + delivery_service - M * (2 - y[key] - delivery_visit),
                        name=f"RouteLinkStart_{sat_id}_{rid}_{truck}",
                    )
                if pickup_node in e1_task_map:
                    pickup_visit = gp.quicksum(
                        x[i, pickup_node, truck]
                        for i in n1e if i != pickup_node
                    )
                    m.addConstr(
                        A_e1[pickup_node, truck] >= route_return[key]
                        - M * (2 - y[key] - pickup_visit),
                        name=f"RouteLinkPickup_{sat_id}_{rid}_{truck}",
                    )

    # Ryan-Foster branching is expressed directly on route membership.  A
    # ``same`` branch requires two tasks to occur on the same selected route;
    # a ``different`` branch forbids every route containing both.  These are
    # valid disjunctions for a set-partitioning route master and will also be
    # passed to the future pricing oracle.
    for branch_idx, (left, right) in enumerate((branch_rules or {}).get("same", [])):
        for sat_id, routes in universe.routes_by_sat.items():
            for rid, route in enumerate(routes):
                if (left in route.tasks) != (right in route.tasks):
                    m.addConstr(y[sat_id, rid] == 0,
                                name=f"RF_Same_{branch_idx}_{sat_id}_{rid}")
    for branch_idx, (left, right) in enumerate((branch_rules or {}).get("different", [])):
        for sat_id, routes in universe.routes_by_sat.items():
            for rid, route in enumerate(routes):
                if left in route.tasks and right in route.tasks:
                    m.addConstr(y[sat_id, rid] == 0,
                                name=f"RF_Different_{branch_idx}_{sat_id}_{rid}")
    return y, route_start, route_return, task_time


def _extract_routes(m, vars_dict, ctx, universe):
    sol_x = m.getAttr("X", vars_dict["x"])
    e1_with_truck = extract_routes_e1(
        sol_x, ctx["depot_id"], ctx["N_E1_Tasks"], ctx["K"]
    )
    e1_routes = [route for _, route in e1_with_truck]
    e2_routes = {sat_id: [] for sat_id in ctx["F"]}
    for sat_id, routes in universe.routes_by_sat.items():
        for rid, route in enumerate(routes):
            if vars_dict["route"][sat_id, rid].X > 0.5:
                e2_routes[sat_id].append(list(route.path))
    return e1_routes, e2_routes


def _solve_master(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                  universe, *, TFp, time_limit, mip_gap, verbose, return_info,
                  allow_restricted_incumbent, branch_rules=None):
    empty_e2 = {sat_id: [] for sat_id in [sat.id for sat in problem.satellites]}
    m, vars_dict, ctx = build_unified_pdp_model(
        problem, e1_tasks, empty_e2, vehicles_per_satellite, TFp=TFp
    )
    # The base builder owns E1.  E2 is supplied entirely by route columns.
    route_vars = _add_route_master(
        m, problem, e2_tasks_by_sat, ctx, vars_dict, universe,
        branch_rules=branch_rules,
    )
    vars_dict.update({
        "route": route_vars[0], "route_start": route_vars[1],
        "route_return": route_vars[2], "e2_task_time": route_vars[3],
    })
    if not verbose:
        m.setParam("OutputFlag", 0)
    m.setParam("MIPGap", mip_gap)
    m.setParam("TimeLimit", time_limit)
    m.optimize()

    if m.status == GRB.OPTIMAL:
        solution_type = (
            "proven_optimal" if universe.complete and not branch_rules
            else "branch_node_optimal" if universe.complete
            else "restricted_route_pool_optimal"
        )
        routes = _extract_routes(m, vars_dict, ctx, universe)
    elif m.SolCount and allow_restricted_incumbent:
        solution_type, routes = "feasible_not_proven", _extract_routes(
            m, vars_dict, ctx, universe
        )
    else:
        solution_type, routes = "none", (None, None)

    info = _solve_info(m, solution_type)
    info.update({
        "route_universe_complete": universe.complete,
        "route_universe_reason": universe.reason,
        "route_count": universe.route_count,
        "route_count_by_satellite": {
            sat_id: len(routes) for sat_id, routes in universe.routes_by_sat.items()
        },
        "branch_rules": branch_rules or {},
    })
    return _maybe_with_info(routes, info, return_info)


def _route_pool_universe(problem, e2_tasks_by_sat, route_paths):
    """Validate a finite route pool without ever treating it as exhaustive."""
    routes_by_sat = {}
    for sat_id, tasks in e2_tasks_by_sat.items():
        task_map = {task.id: task for task in tasks}
        seen_paths = set()
        routes = []
        for raw_path in (route_paths or {}).get(sat_id, []):
            path = tuple(raw_path)
            if (len(path) < 3 or path[0] != sat_id or path[-1] != sat_id
                    or path in seen_paths):
                continue
            task_ids = tuple(path[1:-1])
            if (len(task_ids) != len(set(task_ids))
                    or any(task_id not in task_map for task_id in task_ids)):
                continue
            route_tasks = tuple(task_map[task_id] for task_id in task_ids)
            if (not _route_capacity_feasible(route_tasks, problem.e2_weight_cap,
                                             problem.e2_volume_cap)
                    or not _route_time_feasible(problem, sat_id, route_tasks)):
                continue
            routes.append(E2Route(sat_id=sat_id, path=path, tasks=task_ids))
            seen_paths.add(path)
        missing = sorted(set(task_map) - {
            task_id for route in routes for task_id in route.tasks
        })
        if missing:
            return RouteUniverse(
                routes_by_sat=routes_by_sat, complete=False,
                reason=f"route pool does not cover {sat_id}: {missing}",
            )
        routes_by_sat[sat_id] = routes
    return RouteUniverse(
        routes_by_sat=routes_by_sat, complete=False,
        reason="finite caller-provided route pool",
    )


def solve_route_master_pool(problem, e1_tasks, e2_tasks_by_sat,
                            vehicles_per_satellite, route_paths, *, TFp=9999.0,
                            time_limit=300, mip_gap=0.0, verbose=True,
                            return_info=False,
                            allow_restricted_incumbent=True, branch_rules=None):
    """Optimize a caller-provided route pool without making an exact claim.

    This is the persistent restricted-master API for a future pricing loop.
    Even an ``OPTIMAL`` pool MIP returns ``proven_optimal=False``.
    """
    universe = _route_pool_universe(problem, e2_tasks_by_sat, route_paths)
    if not universe.routes_by_sat:
        info = {
            "status": "INVALID_ROUTE_POOL",
            "solution_type": "restricted_route_pool",
            "proven_optimal": False,
            "route_universe_complete": False,
            "route_universe_reason": universe.reason,
            "route_count": universe.route_count,
        }
        return _maybe_with_info((None, None), info, return_info)
    return _solve_master(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite, universe,
        TFp=TFp, time_limit=time_limit, mip_gap=mip_gap, verbose=verbose,
        return_info=return_info,
        allow_restricted_incumbent=allow_restricted_incumbent,
        branch_rules=branch_rules,
    )


def solve_route_master_exact(problem, e1_tasks, e2_tasks_by_sat,
                             vehicles_per_satellite, *, TFp=9999.0,
                             time_limit=300, mip_gap=0.0, verbose=True,
                             return_info=False, max_tasks_per_satellite=10,
                             max_routes_per_satellite=250_000,
                             allow_restricted_incumbent=False, branch_rules=None):
    """Solve the direct E2 route master; certify only complete universes."""
    universe = enumerate_e2_route_universe(
        problem, e2_tasks_by_sat,
        max_tasks_per_satellite=max_tasks_per_satellite,
        max_routes_per_satellite=max_routes_per_satellite,
    )
    if not universe.complete:
        info = {
            "status": "ROUTE_UNIVERSE_INCOMPLETE",
            "solution_type": "restricted_route_master",
            "proven_optimal": False,
            "route_universe_complete": False,
            "route_universe_reason": universe.reason,
            "route_count": universe.route_count,
        }
        return _maybe_with_info((None, None), info, return_info)
    return _solve_master(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite, universe,
        TFp=TFp, time_limit=time_limit, mip_gap=mip_gap, verbose=verbose,
        return_info=return_info,
        allow_restricted_incumbent=allow_restricted_incumbent,
        branch_rules=branch_rules,
    )
