# -*- coding: utf-8 -*-
"""Joint E1/E2 route-universe formulation for Model 2.

The ordinary Model 2 formulation is an arc-flow MIP.  This module adds a
route-selection view of every E2 route while retaining the original E1/E2
time-linking constraints.  When the route universe is exhaustively generated,
the selected columns and the E2 arc variables are equivalent, so an OPTIMAL
result is a genuine Model 2 certificate.  A truncated universe is explicitly
reported as restricted and is never certified.

It is intentionally bounded to small satellites.  Its role is an independent
exact oracle and the correct master-model foundation for later branch-price,
not a misleading large-instance route-pool heuristic.
"""
from dataclasses import dataclass
from typing import Optional

import gurobipy as gp
from gurobipy import GRB

from .model import build_unified_pdp_model
from .solver import _maybe_with_info, _print_solution, _solve_info


EPS = 1e-9


@dataclass(frozen=True)
class E2Route:
    """One elementary E2 route; service times remain in the joint MIP."""

    sat_id: str
    path: tuple
    tasks: tuple

    @property
    def arcs(self):
        return tuple(zip(self.path, self.path[1:]))


@dataclass
class RouteUniverse:
    routes_by_sat: dict
    complete: bool
    reason: Optional[str] = None

    @property
    def route_count(self):
        return sum(len(routes) for routes in self.routes_by_sat.values())


def _signed_weight(task):
    return task.w if task.kind == "d" else -task.w


def _signed_volume(task):
    return task.v if task.kind == "d" else -task.v


def _route_capacity_feasible(tasks, weight_cap, volume_cap):
    """Check the exact E2 load profile of a candidate elementary sequence."""
    start_weight = sum(task.w for task in tasks if task.kind == "d")
    start_volume = sum(task.v for task in tasks if task.kind == "d")
    if start_weight > weight_cap + EPS or start_volume > volume_cap + EPS:
        return False

    positions = {task.id: idx for idx, task in enumerate(tasks)}
    for task in tasks:
        if task.kind != "p":
            continue
        delivery_id = f"{task.cust}d"
        if delivery_id in positions and positions[delivery_id] > positions[task.id]:
            return False

    load_weight, load_volume = start_weight, start_volume
    for task in tasks:
        load_weight -= _signed_weight(task)
        load_volume -= _signed_volume(task)
        if (load_weight < -EPS or load_volume < -EPS
                or load_weight > weight_cap + EPS
                or load_volume > volume_cap + EPS):
            return False
    return True


def _route_time_feasible(problem, sat_id, tasks):
    """Test whether a sequence has *some* feasible departure from its satellite.

    Starting at time zero and waiting at nodes gives the earliest possible
    service schedule.  If that schedule misses a latest time window, every
    later departure misses it too.  This is only a safe column-generation
    filter; the selected route's actual absolute timing is still enforced by
    the joint MIP.
    """
    current_phys = sat_id
    current_time = 0.0
    current_service = 0.0
    for task in tasks:
        arrival = max(
            task.tw[0],
            current_time + current_service
            + problem.travel_time(current_phys, task.cust),
        )
        if arrival > task.tw[1] + EPS:
            return False
        current_phys = task.cust
        current_time = arrival
        current_service = getattr(problem.nodes[current_phys], "service_time", 0.0)
    return True


def _enumerate_satellite_routes(sat_id, tasks, problem, max_routes):
    """Enumerate all capacity-feasible elementary E2 sequences.

    Time windows deliberately remain in the joint MIP: E2 departure is coupled
    to the E1 arrival time, so filtering paths against an arbitrary standalone
    departure time could remove a feasible joint solution.
    """
    task_by_id = {task.id: task for task in tasks}
    task_ids = tuple(sorted(task_by_id))
    routes = []
    stopped = False

    def visit(path_ids, remaining):
        nonlocal stopped
        if stopped:
            return
        if path_ids:
            route_tasks = tuple(task_by_id[task_id] for task_id in path_ids)
            if not _route_capacity_feasible(
                route_tasks, problem.e2_weight_cap, problem.e2_volume_cap
            ) or not _route_time_feasible(problem, sat_id, route_tasks):
                return
            routes.append(E2Route(
                sat_id=sat_id,
                path=(sat_id,) + tuple(path_ids) + (sat_id,),
                tasks=tuple(path_ids),
            ))
            if len(routes) >= max_routes:
                stopped = True
                return

        for task_id in remaining:
            next_remaining = tuple(item for item in remaining if item != task_id)
            visit(path_ids + (task_id,), next_remaining)
            if stopped:
                return

    visit((), task_ids)
    return routes, not stopped


def enumerate_e2_route_universe(problem, e2_tasks_by_sat, *,
                                max_tasks_per_satellite=10,
                                max_routes_per_satellite=250_000):
    """Build an exhaustive E2 route universe when the configured cap permits."""
    routes_by_sat = {}
    for sat_id, tasks in e2_tasks_by_sat.items():
        if len(tasks) > max_tasks_per_satellite:
            return RouteUniverse(
                routes_by_sat={}, complete=False,
                reason=(f"{sat_id} has {len(tasks)} tasks; exhaustive route "
                        f"enumeration is capped at {max_tasks_per_satellite}"),
            )
        routes, complete = _enumerate_satellite_routes(
            sat_id, tasks, problem, max_routes_per_satellite
        )
        if not complete:
            return RouteUniverse(
                routes_by_sat=routes_by_sat, complete=False,
                reason=(f"{sat_id} reached route cap "
                        f"{max_routes_per_satellite}"),
            )
        routes_by_sat[sat_id] = routes
    return RouteUniverse(routes_by_sat=routes_by_sat, complete=True)


def _add_route_selection_constraints(m, vars_dict, ctx, e2_tasks_by_sat,
                                     universe):
    """Make selected route columns exactly match the original E2 arc flows."""
    y = {}
    z = vars_dict["z"]
    for sat_id, routes in universe.routes_by_sat.items():
        route_ids = range(len(routes))
        for rid in route_ids:
            y[sat_id, rid] = m.addVar(vtype=GRB.BINARY,
                                      name=f"route_select_{sat_id}_{rid}")

        task_ids = [task.id for task in e2_tasks_by_sat.get(sat_id, [])]
        for task_id in task_ids:
            m.addConstr(
                gp.quicksum(
                    y[sat_id, rid] for rid, route in enumerate(routes)
                    if task_id in route.tasks
                ) == 1,
                name=f"RouteCover_{sat_id}_{task_id}",
            )

        m.addConstr(
            gp.quicksum(y[sat_id, rid] for rid in route_ids)
            <= ctx["vehicles_per_satellite"],
            name=f"RouteVehicleLimit_{sat_id}",
        )

        arc_to_routes = {
            (i, j): []
            for i in [sat_id] + task_ids
            for j in [sat_id] + task_ids
            if i != j
        }
        for rid, route in enumerate(routes):
            for arc in route.arcs:
                arc_to_routes[arc].append(rid)

        for (i, j), route_ids_for_arc in arc_to_routes.items():
            e2_arc = gp.quicksum(
                z[i, j, vehicle]
                for vehicle in ctx["V_by_sat"][sat_id]
                if (i, j, vehicle) in z
            )
            selected_arc = gp.quicksum(
                y[sat_id, rid] for rid in route_ids_for_arc
            )
            m.addConstr(e2_arc == selected_arc,
                        name=f"RouteArc_{sat_id}_{i}_{j}")
    return y


def solve_route_universe_exact(problem, e1_tasks, e2_tasks_by_sat,
                               vehicles_per_satellite, *, TFp=9999.0,
                               time_limit=300, mip_gap=0.0, verbose=True,
                               return_info=False,
                               max_tasks_per_satellite=10,
                               max_routes_per_satellite=250_000,
                               allow_restricted_incumbent=False):
    """Solve Model 2 with a provably exhaustive small E2 route universe.

    A non-complete universe is rejected by default.  Setting
    ``allow_restricted_incumbent`` is for diagnostics only and still returns
    ``proven_optimal=False``.
    """
    universe = enumerate_e2_route_universe(
        problem, e2_tasks_by_sat,
        max_tasks_per_satellite=max_tasks_per_satellite,
        max_routes_per_satellite=max_routes_per_satellite,
    )
    if not universe.complete:
        info = {
            "status": "ROUTE_UNIVERSE_INCOMPLETE",
            "solution_type": "restricted_route_universe",
            "proven_optimal": False,
            "route_universe_complete": False,
            "route_universe_reason": universe.reason,
            "route_count": universe.route_count,
        }
        return _maybe_with_info((None, None), info, return_info)

    m, vars_dict, ctx = build_unified_pdp_model(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite, TFp=TFp
    )
    route_select = _add_route_selection_constraints(
        m, vars_dict, ctx, e2_tasks_by_sat, universe
    )
    vars_dict["route_select"] = route_select
    if not verbose:
        m.setParam("OutputFlag", 0)
    m.setParam("MIPGap", mip_gap)
    m.setParam("TimeLimit", time_limit)
    m.optimize()

    if m.status == GRB.OPTIMAL:
        solution_type = "proven_optimal"
        routes = _print_solution(
            m, vars_dict, ctx, e2_tasks_by_sat, problem, verbose=verbose
        )
    elif m.SolCount and allow_restricted_incumbent:
        solution_type = "feasible_not_proven"
        routes = _print_solution(
            m, vars_dict, ctx, e2_tasks_by_sat, problem, verbose=verbose
        )
    else:
        solution_type = "none"
        routes = (None, None)

    info = _solve_info(m, solution_type)
    info.update({
        "route_universe_complete": True,
        "route_universe_reason": None,
        "route_count": universe.route_count,
        "route_count_by_satellite": {
            sat_id: len(routes) for sat_id, routes in universe.routes_by_sat.items()
        },
    })
    return _maybe_with_info(routes, info, return_info)
