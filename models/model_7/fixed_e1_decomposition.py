# -*- coding: utf-8 -*-
"""Small-instance exact oracle for Model 7 with a fixed E1 skeleton.

This module is deliberately *not* a branch-price solver.  It isolates the
decomposition that makes a future exact M7 algorithm possible:

    fixed E1 satellite visit skeleton
        + complete E2 route universe
        = a set-partitioning master with E1 truck-resource rows.

For a fixed E1 skeleton, all E1 routes can be left-shifted to their earliest
schedule without worsening cost, capacity, customer deadlines, or E2
feasibility.  That fixes the earliest E2 departure floor at every visited
satellite.  A selected E2 route then has only constant coefficients in the
master: customer incidence, route count, and the weight/volume carried by the
E1 truck that serves its satellite.

The route universe is enumerated only under explicit safety caps.  Therefore
``fixed_skeleton_proven_optimal`` is true only when the universe is complete
and Gurobi solves the resulting MIP to OPTIMAL.  This is a conditional
certificate for the supplied E1 skeleton, never a global Model-7 certificate;
the latter would additionally have to enumerate/close every E1 skeleton.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Dict, List, Mapping, Optional, Sequence, Tuple, Union

import gurobipy as gp
from gurobipy import GRB

from core.node import Task
from models.model_2.e2_route_cg import (
    _solve_pricing_mip as _solve_e2_pricing_mip,
)


EPS = 1e-8

SkeletonInput = Union[
    "FixedE1Skeleton",
    Mapping[int, Sequence[str]],
    Sequence[Sequence[str]],
]


@dataclass(frozen=True)
class FixedE1Skeleton:
    """Ordered satellite visits for each used E1 truck.

    ``routes`` maps a truck index to an ordered sequence of satellite IDs.
    Satellite IDs are physical IDs (for example ``"S0"``), not ``"S0d"``.
    Empty truck routes are allowed and ignored.
    """

    routes: Mapping[int, Tuple[str, ...]]


@dataclass(frozen=True)
class M7RouteColumn:
    """One complete E2 route feasible after a fixed E1 delivery."""

    sat_id: str
    path: Tuple[str, ...]
    customers: Tuple[str, ...]
    cost: float
    weight: float
    volume: float


@dataclass(frozen=True)
class FixedE1Context:
    """Constant E1 information induced by a validated skeleton."""

    routes: Mapping[int, Tuple[str, ...]]
    truck_of_satellite: Mapping[str, int]
    arrival_at_satellite: Mapping[str, float]
    e2_departure_floor: Mapping[str, float]
    e1_cost: float


def _status_name(status_code: int) -> str:
    names = {
        GRB.OPTIMAL: "OPTIMAL",
        GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.INFEASIBLE: "INFEASIBLE",
        GRB.INF_OR_UNBD: "INF_OR_UNBD",
        GRB.INTERRUPTED: "INTERRUPTED",
        GRB.SUBOPTIMAL: "SUBOPTIMAL",
        GRB.NUMERIC: "NUMERIC",
    }
    return names.get(status_code, str(status_code))


def _satellite_id(problem, node_id: str) -> Optional[str]:
    """Accept a physical satellite ID or the existing ``S?d`` E1 node ID."""
    sat_ids = {sat.id for sat in problem.satellites}
    if node_id in sat_ids:
        return node_id
    if node_id.endswith("d") and node_id[:-1] in sat_ids:
        return node_id[:-1]
    return None


def _normalise_skeleton(problem, skeleton: SkeletonInput) -> FixedE1Skeleton:
    """Normalise convenient user inputs to ``FixedE1Skeleton``.

    Full E1 paths may contain the depot at either end.  Those entries are
    discarded; the remaining entries must be satellite delivery nodes.
    """
    if isinstance(skeleton, FixedE1Skeleton):
        raw_routes = skeleton.routes
    elif isinstance(skeleton, Mapping):
        raw_routes = skeleton
    else:
        raw_routes = {idx: route for idx, route in enumerate(skeleton)}

    depot_id = problem.depot.id
    normalised: Dict[int, Tuple[str, ...]] = {}
    seen_sats = set()
    for raw_truck, raw_route in raw_routes.items():
        truck = int(raw_truck)
        if truck < 0 or truck >= problem.e1_vehicle_count:
            raise ValueError(f"E1 truck index {truck} is outside the fleet")
        sats: List[str] = []
        for node_id in raw_route:
            if node_id == depot_id:
                continue
            sat_id = _satellite_id(problem, str(node_id))
            if sat_id is None:
                raise ValueError(
                    f"Skeleton node {node_id!r} is not a Model-7 satellite delivery node"
                )
            if sat_id in seen_sats:
                raise ValueError(f"Satellite {sat_id} appears more than once in the E1 skeleton")
            seen_sats.add(sat_id)
            sats.append(sat_id)
        if sats:
            normalised[truck] = tuple(sats)
    return FixedE1Skeleton(routes=normalised)


def build_fixed_e1_context(
    problem,
    skeleton: SkeletonInput,
) -> FixedE1Context:
    """Validate a skeleton and compute its earliest E1 delivery schedule.

    E1 has only delivery visits in Model 7.  With all loads non-negative,
    each truck's only relevant load restriction after fixing its satellite
    order is its initial total delivery load; that restriction is added later
    to the E2 route master.
    """
    skeleton = _normalise_skeleton(problem, skeleton)
    depot_id = problem.depot.id
    arrivals: Dict[str, float] = {}
    departure_floors: Dict[str, float] = {}
    truck_of: Dict[str, int] = {}
    e1_cost = 0.0

    for truck, route in sorted(skeleton.routes.items()):
        previous = depot_id
        current_time = 0.0
        for sat_id in route:
            current_time += problem.travel_time(previous, sat_id)
            arrivals[sat_id] = current_time
            service = getattr(problem.nodes[sat_id], "service_time", 0.0)
            departure_floors[sat_id] = current_time + service
            truck_of[sat_id] = truck
            e1_cost += problem.distance(previous, sat_id)
            current_time += service
            previous = sat_id
        if route:
            e1_cost += problem.distance(previous, depot_id)

    return FixedE1Context(
        routes=skeleton.routes,
        truck_of_satellite=truck_of,
        arrival_at_satellite=arrivals,
        e2_departure_floor=departure_floors,
        e1_cost=float(e1_cost),
    )


def _customer_weight(problem, customer_id: str) -> float:
    customer = problem.nodes[customer_id]
    return float(customer.weight_del + problem.fleet.container_weight)


def _customer_volume(problem, customer_id: str) -> float:
    # Model 7, like the existing route-pool formulation, uses one container
    # volume unit for every customer delivery.
    del customer_id
    return float(problem.fleet.container_volume)


def _route_cost(problem, sat_id: str, customer_path: Sequence[str]) -> float:
    path = [sat_id, *customer_path, sat_id]
    return float(sum(problem.distance(path[idx], path[idx + 1])
                     for idx in range(len(path) - 1)))


def _is_customer_allowed_at_satellite(problem, context: FixedE1Context,
                                      customer_id: str, sat_id: str) -> bool:
    customer = problem.nodes[customer_id]
    latest = customer.tw_del[1]
    arrival = context.arrival_at_satellite[sat_id]
    return latest == float("inf") or arrival <= latest + EPS


def _extend_route_if_feasible(problem, sat_id: str, departure_floor: float,
                              customer_path: Tuple[str, ...], candidate: str,
                              current_time: float, current_weight: float,
                              current_volume: float) -> Optional[Tuple[float, float, float]]:
    """Return new route resources or ``None`` if the extension is infeasible.

    ``current_time`` is the completion time at the final customer of the
    partial path.  Waiting is represented by ``max(earliest, arrival)``.
    """
    customer = problem.nodes[candidate]
    if not customer_path:
        predecessor = sat_id
        arrival = departure_floor + problem.travel_time(predecessor, candidate)
    else:
        predecessor = customer_path[-1]
        predecessor_service = getattr(problem.nodes[predecessor], "service_time", 0.0)
        arrival = current_time + predecessor_service + problem.travel_time(predecessor, candidate)
    service_time = max(float(customer.tw_del[0]), float(arrival))
    latest = customer.tw_del[1]
    if latest < float("inf") and service_time > latest + EPS:
        return None

    next_weight = current_weight + _customer_weight(problem, candidate)
    next_volume = current_volume + _customer_volume(problem, candidate)
    if (next_weight > problem.e2_weight_cap + EPS
            or next_volume > problem.e2_volume_cap + EPS):
        return None
    return service_time, next_weight, next_volume


def enumerate_m7_routes_for_fixed_skeleton(
    problem,
    context: FixedE1Context,
    *,
    max_tasks_per_satellite: int = 10,
    max_routes_per_satellite: int = 250_000,
) -> Tuple[Dict[str, List[M7RouteColumn]], bool, Optional[str]]:
    """Enumerate every E2 route feasible after the fixed E1 delivery floor.

    The cap is deliberately conservative.  Reaching it returns
    ``complete=False`` rather than pretending a finite route pool is exact.
    """
    all_customers = [customer.id for customer in problem.customers]
    routes_by_sat: Dict[str, List[M7RouteColumn]] = {}

    for sat_id in sorted(context.truck_of_satellite):
        candidates = [
            customer_id for customer_id in all_customers
            if _is_customer_allowed_at_satellite(problem, context, customer_id, sat_id)
        ]
        if len(candidates) > max_tasks_per_satellite:
            return {}, False, (
                f"{sat_id} has {len(candidates)} candidate customers; exhaustive "
                f"enumeration is capped at {max_tasks_per_satellite}"
            )

        routes: List[M7RouteColumn] = []
        stopped = False
        departure_floor = context.e2_departure_floor[sat_id]

        def visit(path: Tuple[str, ...], remaining: Tuple[str, ...],
                  current_time: float, weight: float, volume: float) -> None:
            nonlocal stopped
            if stopped:
                return
            if path:
                routes.append(M7RouteColumn(
                    sat_id=sat_id,
                    # Keep the public path convention used by Model 1/7:
                    # E2 nodes are task IDs, while ``customers`` remains the
                    # physical-customer incidence vector used by the master.
                    path=(sat_id, *(f"{customer_id}d" for customer_id in path), sat_id),
                    customers=path,
                    cost=_route_cost(problem, sat_id, path),
                    weight=weight,
                    volume=volume,
                ))
                # Deliberately treats equality with the cap as incomplete: it
                # is conservative and avoids an accidental exact claim.
                if len(routes) >= max_routes_per_satellite:
                    stopped = True
                    return

            for customer_id in remaining:
                extended = _extend_route_if_feasible(
                    problem, sat_id, departure_floor, path, customer_id,
                    current_time, weight, volume,
                )
                if extended is None:
                    continue
                service_time, next_weight, next_volume = extended
                next_remaining = tuple(item for item in remaining if item != customer_id)
                visit(path + (customer_id,), next_remaining,
                      service_time, next_weight, next_volume)
                if stopped:
                    return

        visit(tuple(), tuple(sorted(candidates)), departure_floor, 0.0, 0.0)
        if stopped:
            return {}, False, (
                f"{sat_id} reached route cap {max_routes_per_satellite}; "
                "route universe is incomplete"
            )
        routes_by_sat[sat_id] = routes

    return routes_by_sat, True, None


def build_fixed_e1_route_master(
    problem,
    context: FixedE1Context,
    routes_by_sat: Mapping[str, Sequence[M7RouteColumn]],
    *,
    relax: bool = False,
) -> Tuple[gp.Model, Dict[str, object], Dict[str, object]]:
    """Build the exact conditional route master for a complete route universe."""
    columns = [
        column
        for sat_id in sorted(routes_by_sat)
        for column in routes_by_sat[sat_id]
    ]
    route_ids = list(range(len(columns)))
    customers = [customer.id for customer in problem.customers]
    m = gp.Model("M7_FixedE1_CompleteRouteUniverse")
    # A continuous route variable has no explicit upper bound.  The bounds
    # are already implied by non-negative route incidence and Cover_c == 1;
    # omitting the redundant upper bound keeps the usual column-generation
    # reduced-cost formula free of a column-specific upper-bound dual.
    if relax:
        y = m.addVars(route_ids, lb=0.0, vtype=GRB.CONTINUOUS, name="route")
    else:
        y = m.addVars(route_ids, vtype=GRB.BINARY, name="route")

    m.setObjective(
        float(context.e1_cost)
        + gp.quicksum(columns[rid].cost * y[rid] for rid in route_ids),
        GRB.MINIMIZE,
    )

    cover_constrs = {}
    for customer_id in customers:
        cover_constrs[customer_id] = m.addConstr(
            gp.quicksum(y[rid] for rid in route_ids
                        if customer_id in columns[rid].customers) == 1.0,
            name=f"Cover_{customer_id}",
        )

    route_limit_constrs = {}
    route_use_constrs = {}
    for sat_id in sorted(context.truck_of_satellite):
        sat_route_ids = [rid for rid in route_ids if columns[rid].sat_id == sat_id]
        route_limit_constrs[sat_id] = m.addConstr(
            gp.quicksum(y[rid] for rid in sat_route_ids)
            <= problem.fleet.e2_per_sat_count,
            name=f"E2VehicleLimit_{sat_id}",
        )
        # A skeleton visit is valid for Model 7 only when that satellite is
        # actually served.  Otherwise the original served_s/x linking would
        # forbid the visit.
        route_use_constrs[sat_id] = m.addConstr(
            gp.quicksum(y[rid] for rid in sat_route_ids) >= 1.0,
            name=f"ActiveSatelliteUsed_{sat_id}",
        )

    truck_weight_constrs = {}
    truck_volume_constrs = {}
    for truck, route in sorted(context.routes.items()):
        truck_sats = set(route)
        truck_weight_constrs[truck] = m.addConstr(
            gp.quicksum(columns[rid].weight * y[rid] for rid in route_ids
                        if columns[rid].sat_id in truck_sats)
            <= problem.e1_weight_cap,
            name=f"E1TruckWeight_{truck}",
        )
        truck_volume_constrs[truck] = m.addConstr(
            gp.quicksum(columns[rid].volume * y[rid] for rid in route_ids
                        if columns[rid].sat_id in truck_sats)
            <= problem.e1_volume_cap,
            name=f"E1TruckVolume_{truck}",
        )

    return m, {"route": y}, {
        "columns": columns,
        "cover": cover_constrs,
        "route_limit": route_limit_constrs,
        "route_use": route_use_constrs,
        "truck_weight": truck_weight_constrs,
        "truck_volume": truck_volume_constrs,
    }


def _extract_solution(problem, context: FixedE1Context,
                      variables: Mapping[str, object],
                      metadata: Mapping[str, object]) -> Dict[str, object]:
    columns: Sequence[M7RouteColumn] = metadata["columns"]  # type: ignore[assignment]
    y = variables["route"]
    e2_routes = {sat.id: [] for sat in problem.satellites}
    assignment: Dict[str, str] = {}
    for rid, column in enumerate(columns):
        if y[rid].X <= 0.5:
            continue
        e2_routes[column.sat_id].append(list(column.path))
        for customer_id in column.customers:
            assignment[customer_id] = column.sat_id

    depot_id = problem.depot.id
    e1_routes = [
        [depot_id, *(f"{sat_id}d" for sat_id in route), depot_id]
        for _, route in sorted(context.routes.items()) if route
    ]
    return {
        "e1_routes": e1_routes,
        "e2_routes": e2_routes,
        "assignment": assignment,
    }


def solve_fixed_e1_skeleton_complete_universe(
    problem,
    skeleton: SkeletonInput,
    *,
    max_tasks_per_satellite: int = 10,
    max_routes_per_satellite: int = 250_000,
    time_limit: float = 300.0,
    mip_gap: float = 0.0,
    verbose: bool = False,
) -> Dict[str, object]:
    """Solve the conditional M7 problem exactly when route enumeration permits.

    ``proven_optimal`` intentionally remains false: only the supplied E1
    skeleton has been closed.  Callers must close every E1 skeleton before
    upgrading the result to a global Model-7 proof.
    """
    try:
        context = build_fixed_e1_context(problem, skeleton)
    except ValueError as exc:
        return {
            "status": "INVALID_E1_SKELETON",
            "proven_optimal": False,
            "fixed_skeleton_proven_optimal": False,
            "message": str(exc),
        }

    routes_by_sat, universe_complete, reason = enumerate_m7_routes_for_fixed_skeleton(
        problem,
        context,
        max_tasks_per_satellite=max_tasks_per_satellite,
        max_routes_per_satellite=max_routes_per_satellite,
    )
    result: Dict[str, object] = {
        "status": "ROUTE_UNIVERSE_INCOMPLETE" if not universe_complete else None,
        "proven_optimal": False,
        "fixed_skeleton_proven_optimal": False,
        "solution_type": "fixed_e1_skeleton_complete_universe",
        "route_universe_complete": universe_complete,
        "route_universe_reason": reason,
        "e1_cost": context.e1_cost,
        "e1_arrival_at_satellite": dict(context.arrival_at_satellite),
        "e2_departure_floor": dict(context.e2_departure_floor),
        "route_count_by_satellite": {
            sat_id: len(routes) for sat_id, routes in routes_by_sat.items()
        },
        "route_count": sum(len(routes) for routes in routes_by_sat.values()),
    }
    if not universe_complete:
        return result

    m, variables, metadata = build_fixed_e1_route_master(
        problem, context, routes_by_sat,
    )
    m.Params.OutputFlag = 1 if verbose else 0
    m.Params.TimeLimit = float(time_limit)
    m.Params.MIPGap = float(mip_gap)
    m.optimize()

    result.update({
        "status": _status_name(m.status),
        "status_code": int(m.status),
        "runtime": float(getattr(m, "Runtime", 0.0)),
        "sol_count": int(getattr(m, "SolCount", 0)),
    })
    if m.SolCount:
        result["objective"] = float(m.ObjVal)
        result["mip_gap"] = float(m.MIPGap)
        result.update(_extract_solution(problem, context, variables, metadata))
    try:
        result["best_bound"] = float(m.ObjBound)
    except Exception:
        pass
    if m.status == GRB.OPTIMAL:
        result["fixed_skeleton_proven_optimal"] = True
        result["solution_type"] = "fixed_e1_skeleton_complete_universe_optimal"
    return result


# ---------------------------------------------------------------------------
# Fixed-skeleton root column generation
# ---------------------------------------------------------------------------

# The following root-CG implementation is intentionally kept separate from
# ``solve_fixed_e1_skeleton_complete_universe`` above.  CG closes the *LP*
# over the full route family without enumerating that family.  It can therefore
# certify only the fixed-skeleton LP bound; solving an integer RMP afterwards
# produces a useful incumbent, not an integer proof.


def _m7_delivery_tasks(problem) -> List[Task]:
    """Create the pure-delivery task view expected by M2's E2 pricing MIP."""
    container_weight = float(problem.fleet.container_weight)
    container_volume = float(problem.fleet.container_volume)
    return [
        Task(
            f"{customer.id}d",
            customer.id,
            "d",
            float(customer.weight_del) + container_weight,
            container_volume,
            customer.tw_del,
        )
        for customer in problem.customers
    ]


def _column_key(column: M7RouteColumn) -> Tuple[str, Tuple[str, ...]]:
    return column.sat_id, tuple(column.path)


def _pricing_column_to_m7(
    pricing_column,
    task_map: Mapping[str, Task],
) -> M7RouteColumn:
    """Convert the generic E2 pricing column to the fixed-skeleton format."""
    tasks = tuple(task_id for task_id in pricing_column.tasks if task_id in task_map)
    customers = tuple(task_map[task_id].cust for task_id in tasks)
    return M7RouteColumn(
        sat_id=pricing_column.sat_id,
        path=tuple(pricing_column.path),
        customers=customers,
        cost=float(pricing_column.cost),
        weight=float(sum(task_map[task_id].w for task_id in tasks)),
        volume=float(sum(task_map[task_id].v for task_id in tasks)),
    )


def _build_fixed_e1_cg_master(
    problem,
    context: FixedE1Context,
    columns_by_sat: Mapping[str, Sequence[M7RouteColumn]],
    artificial_cost: float,
) -> Tuple[gp.Model, Dict[str, object], Dict[str, object]]:
    """Build a feasible RMP with only transparent phase-zero artificials.

    The real route part is exactly ``build_fixed_e1_route_master(...,
    relax=True)``.  Artificial cover and active-satellite variables are only
    a way to obtain a first LP dual before enough physical routes have been
    priced.  A root-LP certificate is issued only after every artificial is
    zero, so they never alter a certified result.
    """
    master, variables, metadata = build_fixed_e1_route_master(
        problem,
        context,
        columns_by_sat,
        relax=True,
    )
    route_vars = variables["route"]
    columns: Sequence[M7RouteColumn] = metadata["columns"]  # type: ignore[assignment]
    customers = [customer.id for customer in problem.customers]
    active_sats = sorted(context.truck_of_satellite)

    artificial_cover = master.addVars(
        customers,
        lb=0.0,
        vtype=GRB.CONTINUOUS,
        name="artificial_cover",
    )
    artificial_active = master.addVars(
        active_sats,
        lb=0.0,
        vtype=GRB.CONTINUOUS,
        name="artificial_active_satellite",
    )
    for customer_id in customers:
        master.chgCoeff(metadata["cover"][customer_id], artificial_cover[customer_id], 1.0)
    for sat_id in active_sats:
        master.chgCoeff(metadata["route_use"][sat_id], artificial_active[sat_id], 1.0)

    master.setObjective(
        float(context.e1_cost)
        + gp.quicksum(column.cost * route_vars[rid]
                      for rid, column in enumerate(columns))
        + float(artificial_cost) * (
            gp.quicksum(artificial_cover[customer_id] for customer_id in customers)
            + gp.quicksum(artificial_active[sat_id] for sat_id in active_sats)
        ),
        GRB.MINIMIZE,
    )
    master.update()
    variables = dict(variables)
    variables["artificial_cover"] = artificial_cover
    variables["artificial_active"] = artificial_active
    metadata = dict(metadata)
    metadata["artificial_cost"] = float(artificial_cost)
    return master, variables, metadata


def _extract_fixed_e1_cg_duals(metadata: Mapping[str, object]) -> Dict[str, Dict]:
    """Read precisely the dual rows that define a fixed-skeleton route RC."""
    return {
        "cover": {
            customer_id: float(constr.Pi)
            for customer_id, constr in metadata["cover"].items()
        },
        "route_limit": {
            sat_id: float(constr.Pi)
            for sat_id, constr in metadata["route_limit"].items()
        },
        "route_use": {
            sat_id: float(constr.Pi)
            for sat_id, constr in metadata["route_use"].items()
        },
        "truck_weight": {
            truck: float(constr.Pi)
            for truck, constr in metadata["truck_weight"].items()
        },
        "truck_volume": {
            truck: float(constr.Pi)
            for truck, constr in metadata["truck_volume"].items()
        },
    }


def _fixed_e1_route_reduced_cost(
    context: FixedE1Context,
    column: M7RouteColumn,
    duals: Mapping[str, Mapping],
) -> float:
    """Return c_r - A_r^T pi for the exact fixed-skeleton LP master.

    Row signs are intentionally not hard-coded here.  Gurobi's ``Pi`` is
    used directly with the route coefficient in each row, which gives:

    ``cost - cover - vehicle_limit - active_satellite
            - E1_weight * route_weight - E1_volume * route_volume``.
    """
    truck = context.truck_of_satellite[column.sat_id]
    return float(
        column.cost
        - sum(float(duals["cover"][customer_id]) for customer_id in column.customers)
        - float(duals["route_limit"][column.sat_id])
        - float(duals["route_use"][column.sat_id])
        - float(duals["truck_weight"][truck]) * column.weight
        - float(duals["truck_volume"][truck]) * column.volume
    )


def _fixed_e1_pricing_terms(
    problem,
    context: FixedE1Context,
    sat_id: str,
    duals: Mapping[str, Mapping],
) -> Tuple[Dict[str, float], float]:
    """Map all additive fixed-master duals into M2's E2 pricing interface.

    ``_solve_e2_pricing_mip`` minimizes
    ``route_cost - sum(task_reward[t] * y[t]) - route_constant``.  E1 truck
    capacity coefficients are additive across delivery tasks, so their duals
    can be folded into the task reward without losing exactness.
    """
    truck = context.truck_of_satellite[sat_id]
    weight_pi = float(duals["truck_weight"][truck])
    volume_pi = float(duals["truck_volume"][truck])
    task_rewards = {}
    for customer in problem.customers:
        task_id = f"{customer.id}d"
        task_rewards[task_id] = float(duals["cover"][customer.id]) + (
            weight_pi * _customer_weight(problem, customer.id)
            + volume_pi * _customer_volume(problem, customer.id)
        )
    route_constant = (
        float(duals["route_limit"][sat_id])
        + float(duals["route_use"][sat_id])
    )
    return task_rewards, route_constant


def _verify_fixed_e1_master_reduced_costs(
    context: FixedE1Context,
    variables: Mapping[str, object],
    metadata: Mapping[str, object],
    duals: Mapping[str, Mapping],
) -> Dict[str, object]:
    """Cross-check every current column against Gurobi's own LP RC value."""
    route_vars = variables["route"]
    columns: Sequence[M7RouteColumn] = metadata["columns"]  # type: ignore[assignment]
    checks = []
    max_error = 0.0
    for rid, column in enumerate(columns):
        formula_rc = _fixed_e1_route_reduced_cost(context, column, duals)
        solver_rc = float(route_vars[rid].RC)
        error = abs(formula_rc - solver_rc)
        max_error = max(max_error, error)
        checks.append({
            "route_id": rid,
            "satellite": column.sat_id,
            "path": list(column.path),
            "formula_reduced_cost": formula_rc,
            "gurobi_reduced_cost": solver_rc,
            "absolute_error": error,
        })
    return {
        "checked_columns": len(checks),
        "max_absolute_error": max_error,
        "checks": checks,
    }


def _serialize_m7_columns(columns_by_sat: Mapping[str, Sequence[M7RouteColumn]]) -> List[Dict[str, object]]:
    return [
        {
            "sat_id": column.sat_id,
            "path": list(column.path),
            "customers": list(column.customers),
            "cost": column.cost,
            "weight": column.weight,
            "volume": column.volume,
        }
        for sat_id in sorted(columns_by_sat)
        for column in columns_by_sat[sat_id]
    ]


def _solve_fixed_e1_restricted_ip(
    problem,
    context: FixedE1Context,
    columns_by_sat: Mapping[str, Sequence[M7RouteColumn]],
    *,
    time_limit: float,
    verbose: bool,
) -> Dict[str, object]:
    """Solve the current finite pool as an incumbent generator only."""
    result: Dict[str, object] = {
        "solution_type": "fixed_e1_restricted_route_pool_incumbent",
        "is_fixed_skeleton_integer_proof": False,
        "is_global_proof": False,
        "route_column_count": sum(len(columns) for columns in columns_by_sat.values()),
    }
    master, variables, metadata = build_fixed_e1_route_master(
        problem,
        context,
        columns_by_sat,
        relax=False,
    )
    master.Params.OutputFlag = 1 if verbose else 0
    master.Params.TimeLimit = max(0.0, float(time_limit))
    master.Params.MIPGap = 0.0
    master.optimize()
    result.update({
        "status": _status_name(master.status),
        "status_code": int(master.status),
        "runtime": float(getattr(master, "Runtime", 0.0)),
        "sol_count": int(getattr(master, "SolCount", 0)),
    })
    if master.SolCount:
        result["objective"] = float(master.ObjVal)
        result["mip_gap"] = float(master.MIPGap)
        result.update(_extract_solution(problem, context, variables, metadata))
    try:
        result["restricted_pool_best_bound"] = float(master.ObjBound)
    except Exception:
        pass
    return result


def solve_fixed_e1_skeleton_root_cg(
    problem,
    skeleton: SkeletonInput,
    *,
    max_iterations: int = 200,
    time_limit: float = 600.0,
    master_time_limit: float = 60.0,
    pricing_time_limit: float = 60.0,
    pricing_pool_solutions: int = 10,
    artificial_cost: float = 100_000.0,
    reduced_cost_tolerance: float = 1e-6,
    dual_check_tolerance: float = 1e-6,
    solve_restricted_ip: bool = True,
    restricted_ip_time_limit: float = 300.0,
    verbose: bool = False,
) -> Dict[str, object]:
    """Close the *root LP* for one fixed M7 E1 skeleton by exact pricing.

    This is a sound decomposition because the fixed skeleton turns every
    route coefficient into a constant: global customer cover, per-satellite
    E2 vehicle count, active-satellite use, and E1-truck weight/volume.
    The M2 E2 pricing MIP is reused with ``s_max_floor`` set to the E1-ready
    time and with the E1 resource duals folded into additive task rewards.

    The result deliberately never sets ``proven_optimal`` or
    ``fixed_skeleton_proven_optimal``.  At most it sets
    ``fixed_skeleton_root_lp_proven``: an LP certificate conditional on the
    supplied E1 skeleton.  The optional integer solve is only an incumbent
    over the currently generated finite route pool.
    """
    started = time.time()
    try:
        context = build_fixed_e1_context(problem, skeleton)
    except ValueError as exc:
        return {
            "status": "INVALID_E1_SKELETON",
            "message": str(exc),
            "proven_optimal": False,
            "fixed_skeleton_proven_optimal": False,
            "fixed_skeleton_root_lp_proven": False,
        }

    if not context.truck_of_satellite:
        return {
            "status": "EMPTY_E1_SKELETON",
            "message": "A Model-7 solution with customers cannot use an empty E1 skeleton",
            "proven_optimal": False,
            "fixed_skeleton_proven_optimal": False,
            "fixed_skeleton_root_lp_proven": False,
            "e1_cost": context.e1_cost,
        }

    tasks = _m7_delivery_tasks(problem)
    task_map = {task.id: task for task in tasks}
    active_sats = sorted(context.truck_of_satellite)
    columns_by_sat: Dict[str, List[M7RouteColumn]] = {
        sat_id: [] for sat_id in active_sats
    }
    seen_columns = set()
    pricing_history: List[Dict[str, object]] = []
    dual_checks: List[Dict[str, object]] = []
    last_master = None
    last_variables = None
    last_metadata = None
    last_duals = None
    final_artificial_sum = None
    final_real_lp_objective = None
    fixed_skeleton_root_lp_proven = False
    status = "ITERATION_LIMIT"
    message = None

    for iteration in range(max(0, int(max_iterations))):
        elapsed = time.time() - started
        if elapsed >= time_limit:
            status = "TIME_LIMIT"
            message = "overall fixed-skeleton root-CG time limit reached"
            break

        master, variables, metadata = _build_fixed_e1_cg_master(
            problem,
            context,
            columns_by_sat,
            artificial_cost,
        )
        master.Params.OutputFlag = 1 if verbose else 0
        master.Params.TimeLimit = min(float(master_time_limit), max(0.0, time_limit - elapsed))
        master.optimize()
        last_master, last_variables, last_metadata = master, variables, metadata
        if master.status != GRB.OPTIMAL:
            status = f"MASTER_{_status_name(master.status)}"
            message = "restricted LP did not solve to optimality"
            break

        artificial_cover = variables["artificial_cover"]
        artificial_active = variables["artificial_active"]
        cover_artificial_sum = float(sum(var.X for var in artificial_cover.values()))
        active_artificial_sum = float(sum(var.X for var in artificial_active.values()))
        final_artificial_sum = cover_artificial_sum + active_artificial_sum
        columns: Sequence[M7RouteColumn] = metadata["columns"]  # type: ignore[assignment]
        route_vars = variables["route"]
        final_real_lp_objective = float(
            context.e1_cost
            + sum(column.cost * route_vars[rid].X for rid, column in enumerate(columns))
        )
        duals = _extract_fixed_e1_cg_duals(metadata)
        last_duals = duals
        dual_check = _verify_fixed_e1_master_reduced_costs(
            context,
            variables,
            metadata,
            duals,
        )
        dual_checks.append({
            "iteration": iteration,
            **dual_check,
        })
        if dual_check["max_absolute_error"] > dual_check_tolerance:
            status = "REDUCED_COST_DUAL_MISMATCH"
            message = (
                "fixed-master formula reduced costs do not match Gurobi RC; "
                "refusing to certify pricing"
            )
            break

        iteration_pricing: Dict[str, object] = {
            "iteration": iteration,
            "master_objective_with_artificials": float(master.ObjVal),
            "master_objective_without_artificials": final_real_lp_objective,
            "cover_artificial_sum": cover_artificial_sum,
            "active_artificial_sum": active_artificial_sum,
            "satellites": {},
        }
        all_pricing_closed = True
        added_this_iteration = 0
        duplicate_negative_route = False
        for sat_id in active_sats:
            remaining = time_limit - (time.time() - started)
            if remaining <= 0.0:
                all_pricing_closed = False
                iteration_pricing["satellites"][sat_id] = {
                    "status": "TIME_LIMIT_BEFORE_PRICING",
                }
                continue
            task_rewards, route_constant = _fixed_e1_pricing_terms(
                problem,
                context,
                sat_id,
                duals,
            )
            pricing_columns, pricing_info = _solve_e2_pricing_mip(
                problem,
                sat_id,
                tasks,
                task_rewards,
                route_constant,
                pricing_time_limit=min(float(pricing_time_limit), remaining),
                pool_solutions=max(1, int(pricing_pool_solutions)),
                output_flag=verbose,
                s_max_floor=context.e2_departure_floor[sat_id],
            )
            price_record: Dict[str, object] = dict(pricing_info)
            price_record["ready_time_floor"] = context.e2_departure_floor[sat_id]
            price_record["route_constant_dual"] = route_constant
            price_record["task_reward_duals"] = dict(task_rewards)
            price_record["generated_columns"] = []

            # A proven-infeasible route MIP means this satellite has no
            # feasible nonempty route; that also closes its pricing problem.
            pricing_closed = pricing_info.get("status") == "INFEASIBLE"
            if pricing_info.get("status") == "OPTIMAL":
                pricing_closed = (
                    float(pricing_info.get("reduced_cost_bound", float("-inf")))
                    >= -reduced_cost_tolerance
                )
            if not pricing_closed:
                all_pricing_closed = False

            for pricing_column in pricing_columns:
                column = _pricing_column_to_m7(pricing_column, task_map)
                formula_rc = _fixed_e1_route_reduced_cost(context, column, duals)
                price_record["generated_columns"].append({
                    "path": list(column.path),
                    "formula_reduced_cost": formula_rc,
                    "pricing_reduced_cost": pricing_column.cost
                    - sum(task_rewards[task_id] for task_id in pricing_column.tasks)
                    - route_constant,
                })
                # The low-level pricing routine only returns routes it regards
                # as negative.  Re-evaluate from the master rows before adding
                # one, so an accidental interface/sign mismatch cannot sneak
                # into the RMP.
                if formula_rc >= -reduced_cost_tolerance:
                    continue
                key = _column_key(column)
                if key in seen_columns:
                    duplicate_negative_route = True
                    continue
                seen_columns.add(key)
                columns_by_sat[sat_id].append(column)
                added_this_iteration += 1
            iteration_pricing["satellites"][sat_id] = price_record

        iteration_pricing["columns_added"] = added_this_iteration
        pricing_history.append(iteration_pricing)
        if duplicate_negative_route:
            status = "DUPLICATE_NEGATIVE_ROUTE"
            message = (
                "pricing returned an already-present route with materially "
                "negative reduced cost; refusing to claim a root-LP proof"
            )
            break
        if added_this_iteration:
            continue
        if all_pricing_closed:
            if final_artificial_sum is not None and final_artificial_sum <= reduced_cost_tolerance:
                fixed_skeleton_root_lp_proven = True
                status = "FIXED_SKELETON_ROOT_LP_PROVED"
            else:
                status = "FIXED_SKELETON_LP_ARTIFICIALS_REMAIN"
                message = (
                    "pricing closed for the augmented RMP, but artificial "
                    "cover/activation variables remain; no real fixed-skeleton "
                    "LP certificate is issued"
                )
            break
        status = "PRICING_NOT_PROVED"
        message = "at least one satellite pricing MIP did not close to optimality"
        break
    else:
        status = "ITERATION_LIMIT"
        message = "fixed-skeleton root-CG iteration limit reached"

    result: Dict[str, object] = {
        "status": status,
        "message": message,
        "solution_type": "fixed_e1_skeleton_root_column_generation",
        "proven_optimal": False,
        "fixed_skeleton_proven_optimal": False,
        "fixed_skeleton_root_lp_proven": fixed_skeleton_root_lp_proven,
        "route_universe_complete": False,
        "e1_cost": context.e1_cost,
        "e1_arrival_at_satellite": dict(context.arrival_at_satellite),
        "e2_departure_floor": dict(context.e2_departure_floor),
        "route_column_count": sum(len(columns) for columns in columns_by_sat.values()),
        "route_column_count_by_satellite": {
            sat_id: len(columns_by_sat[sat_id]) for sat_id in active_sats
        },
        "route_columns": _serialize_m7_columns(columns_by_sat),
        "iterations": len(pricing_history),
        "pricing_history": pricing_history,
        "dual_reduced_cost_checks": dual_checks,
        "artificial_sum": final_artificial_sum,
        "lp_objective_without_artificials": final_real_lp_objective,
        "runtime": time.time() - started,
        "reduced_cost_tolerance": reduced_cost_tolerance,
        "dual_check_tolerance": dual_check_tolerance,
    }
    if last_master is not None and last_master.SolCount:
        result["last_master_objective_with_artificials"] = float(last_master.ObjVal)
        result["last_master_status"] = _status_name(last_master.status)
    if last_duals is not None:
        result["last_master_duals"] = last_duals

    if solve_restricted_ip:
        remaining = max(0.0, time_limit - (time.time() - started))
        result["restricted_ip_incumbent"] = _solve_fixed_e1_restricted_ip(
            problem,
            context,
            columns_by_sat,
            time_limit=min(float(restricted_ip_time_limit), remaining),
            verbose=verbose,
        )
    return result
