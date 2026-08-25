"""Import a validated fixed-assignment Model-1 solution into M2 time expansion.

Model 2 is Model 1 with customer-to-satellite assignment made optional.  A
feasible Model-1 solution built with the canonical fixed assignment is thus a
feasible Model-2 solution with the same physical routes and cost.  This file
turns that observation into a checked warm-start/upper-bound handoff for the
time-expanded M2 master.

The handoff is deliberately conservative:

* every E1/E2 path, capacity, customer coverage, assignment, deadline, and
  ready-state mapping is revalidated from the routes;
* E2 columns are scoped to the *particular* ready state induced by their M1
  E1 prefix, rather than being silently copied to every state;
* the result is a feasible M2 incumbent/upper bound only.  It is never an M2
  optimality certificate, even if the source Model-1 solution was optimal.
"""

from __future__ import annotations

from math import isfinite
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from models.model_2.certified_time_expansion import (
    EPS,
    ReadyState,
    enumerate_ready_states,
    forward_m2_route,
)


def import_m1_solution_as_m2_time_expanded_incumbent(
    problem,
    e1_routes: Iterable[Sequence[str]],
    e2_routes_by_sat: Mapping[str, Iterable[Sequence[str]]],
    *,
    ready_states: Mapping[str, Sequence[ReadyState]] | None = None,
    source_info: Mapping[str, Any] | None = None,
    require_fixed_assignment_match: bool = True,
    objective_tolerance: float = 1e-4,
    eps: float = EPS,
) -> Dict[str, Any]:
    """Validate and convert one M1 route solution into state-scoped M2 columns.

    Parameters
    ----------
    problem:
        The same canonical instance used by Model 1.  Its customer
        ``assigned_sat`` values define Model 1's fixed assignment when
        ``require_fixed_assignment_match`` is true.
    e1_routes, e2_routes_by_sat:
        The paths returned by ``models.model_1.solver.solve_exact`` or
        ``solve_mip``.  E1 satellite node IDs may be physical (``S0``) or
        logical delivery IDs (``S0d``); E2 interiors must be ``C?d`` tasks.
    ready_states:
        Normally omitted, in which case the complete M2 no-wait state family
        is enumerated.  Supplying a reduced family is accepted only if it
        still contains each source E1 prefix's exact no-wait completion time.
        This permits the opt-in canonical state family, which merges distinct
        diagnostic prefixes with identical times.
    source_info:
        Optional Model-1 solver metadata.  Its objective is cross-checked
        when present, but its ``proven_optimal`` flag is informational only.

    Returns
    -------
    dict
        Includes ``route_columns`` suitable for
        ``solve_m2_time_expanded_root_cg(initial_route_columns=...)`` and the
        explicit M2-feasible routes/cost.  ``proven_optimal`` is always false.

    Raises
    ------
    ValueError
        If the supplied routes are not a valid canonical Model-1 solution or
        cannot be represented by the supplied ready-state family.
    """
    if objective_tolerance < 0.0:
        raise ValueError("objective_tolerance must be non-negative")

    depot_id = problem.depot.id
    sat_ids = {sat.id for sat in problem.satellites}
    customer_by_id = {customer.id: customer for customer in problem.customers}
    customer_ids = set(customer_by_id)
    states = _coerce_ready_states(problem, ready_states)

    normalised_e1, state_selection, ready_times, sat_to_e1_route = _normalise_e1_routes(
        problem,
        e1_routes,
        states,
    )
    if len(normalised_e1) > problem.e1_vehicle_count:
        raise ValueError("source uses more E1 routes than the available fleet")

    (
        normalised_e2,
        assignment,
        route_columns,
        route_schedules,
    ) = _normalise_e2_routes(
        problem,
        e2_routes_by_sat,
        states,
        state_selection,
        ready_times,
        customer_by_id,
        eps,
    )

    covered_customers = set(assignment)
    missing = sorted(customer_ids - covered_customers)
    if missing:
        raise ValueError(f"source E2 routes do not cover customers: {missing}")
    unexpected = sorted(covered_customers - customer_ids)
    if unexpected:
        raise ValueError(f"source E2 routes contain unknown customers: {unexpected}")

    used_e2_sats = {sat_id for sat_id, routes in normalised_e2.items() if routes}
    used_e1_sats = set(state_selection)
    if used_e2_sats != used_e1_sats:
        raise ValueError(
            "M2 requires an E1 delivery iff the satellite serves at least one "
            f"customer; E1={sorted(used_e1_sats)}, E2={sorted(used_e2_sats)}"
        )
    if not used_e1_sats and customer_ids:
        raise ValueError("customers exist but the source has no active E1/E2 route")

    for sat_id, routes in normalised_e2.items():
        if len(routes) > problem.fleet.e2_per_sat_count:
            raise ValueError(
                f"{sat_id!r} uses {len(routes)} E2 routes, above the fleet limit "
                f"{problem.fleet.e2_per_sat_count}"
            )

    if require_fixed_assignment_match:
        mismatches = []
        for customer_id, sat_id in sorted(assignment.items()):
            expected = customer_by_id[customer_id].assigned_sat
            if expected != sat_id:
                mismatches.append((customer_id, expected, sat_id))
        if mismatches:
            raise ValueError(
                "source does not match the instance's fixed Model-1 assignment: "
                f"{mismatches[:5]}"
            )

    _validate_e1_loads_and_due_dates(
        problem,
        normalised_e1,
        assignment,
        ready_times,
        sat_to_e1_route,
        customer_by_id,
        eps,
    )

    e1_cost = sum(_e1_route_distance(problem, route) for route in normalised_e1)
    e2_cost = sum(
        float(schedule["distance"])
        for schedules in route_schedules.values()
        for schedule in schedules
    )
    m2_cost = float(e1_cost + e2_cost)
    reported_source_objective = _source_objective(source_info)
    if reported_source_objective is not None and abs(m2_cost - reported_source_objective) > objective_tolerance:
        raise ValueError(
            "source solver objective does not match the reconstructed route cost: "
            f"reported={reported_source_objective:.9f}, reconstructed={m2_cost:.9f}"
        )

    return {
        "status": "VALIDATED_M1_IMPORT",
        "solution_type": "validated_m1_fixed_assignment_m2_incumbent",
        "proof_scope": "m1_to_m2_feasible_incumbent_only",
        "proven_optimal": False,
        "global_integer_optimum_proven": False,
        "route_universe_complete": False,
        "m2_feasible_upper_bound": m2_cost,
        "source_m1_objective": reported_source_objective,
        "source_m1_proven_optimal": bool((source_info or {}).get("proven_optimal", False)),
        "source_m1_solution_type": (source_info or {}).get("solution_type"),
        "e1_cost": e1_cost,
        "e2_cost": e2_cost,
        "e1_routes": [list(route) for route in normalised_e1],
        "e2_routes": {
            sat_id: [list(route) for route in routes]
            for sat_id, routes in normalised_e2.items()
        },
        "assignment": dict(sorted(assignment.items())),
        "ready_state_selection": dict(sorted(state_selection.items())),
        "ready_times": dict(sorted(ready_times.items())),
        "ready_states": states,
        # Each column has one state_idx.  This preserves the master/pricing
        # contract, unlike an unscoped route which is expanded at every state.
        "route_columns": route_columns,
        "route_schedules": route_schedules,
        "validation": {
            "customer_count": len(customer_ids),
            "e1_route_count": len(normalised_e1),
            "e2_route_count": sum(len(routes) for routes in normalised_e2.values()),
            "fixed_assignment_match_required": require_fixed_assignment_match,
            "source_objective_checked": reported_source_objective is not None,
        },
    }


def _coerce_ready_states(
    problem,
    ready_states: Mapping[str, Sequence[ReadyState]] | None,
) -> Dict[str, Tuple[ReadyState, ...]]:
    raw_states = enumerate_ready_states(problem) if ready_states is None else ready_states
    result: Dict[str, Tuple[ReadyState, ...]] = {}
    for satellite in problem.satellites:
        sat_id = satellite.id
        values = tuple(raw_states.get(sat_id, ()))
        if not values:
            raise ValueError(f"no ready states supplied for satellite {sat_id!r}")
        for state in values:
            if state.sat_id != sat_id:
                raise ValueError(f"ready state {state!r} belongs to the wrong satellite")
        result[sat_id] = values
    return result


def _normalise_e1_routes(
    problem,
    e1_routes: Iterable[Sequence[str]],
    states: Mapping[str, Sequence[ReadyState]],
) -> Tuple[
    Tuple[Tuple[str, ...], ...],
    Dict[str, int],
    Dict[str, float],
    Dict[str, int],
]:
    depot_id = problem.depot.id
    sat_ids = {sat.id for sat in problem.satellites}
    state_index = {
        (sat_id, tuple(state.prefix)): index
        for sat_id, items in states.items()
        for index, state in enumerate(items)
    }
    # A canonical state family retains only one representative prefix at an
    # exactly equal ready time.  The time is the modelling-relevant part of a
    # state, so retain a fallback index by exact float value for that mode.
    state_index_by_time = {
        (sat_id, float(state.ready_time)): index
        for sat_id, items in states.items()
        for index, state in enumerate(items)
    }
    result = []
    state_selection: Dict[str, int] = {}
    ready_times: Dict[str, float] = {}
    sat_to_route: Dict[str, int] = {}

    for route_index, raw_route in enumerate(e1_routes or ()):
        items = tuple(str(node_id) for node_id in raw_route)
        if len(items) < 3 or items[0] != depot_id or items[-1] != depot_id:
            raise ValueError(f"invalid E1 route endpoints: {items!r}")
        physical_route = [depot_id]
        for node_id in items[1:-1]:
            sat_id = _physical_satellite_id(node_id, sat_ids)
            if sat_id is None:
                raise ValueError(f"E1 route contains a non-delivery satellite node {node_id!r}")
            if sat_id in state_selection:
                raise ValueError(f"satellite {sat_id!r} appears in more than one E1 route")
            physical_route.append(sat_id)
            prefix = tuple(physical_route)
            state_idx = state_index.get((sat_id, prefix))
            if state_idx is None:
                prefix_time = _no_wait_prefix_ready_time(problem, prefix)
                state_idx = state_index_by_time.get((sat_id, prefix_time))
            if state_idx is None:
                raise ValueError(
                    "ready-state family lacks the exact no-wait completion "
                    f"time for source prefix {prefix!r}"
                )
            state = states[sat_id][state_idx]
            state_selection[sat_id] = state_idx
            ready_times[sat_id] = float(state.ready_time)
            sat_to_route[sat_id] = route_index
        physical_route.append(depot_id)
        if len(physical_route) < 3:
            raise ValueError(f"empty E1 route is not a valid source path: {items!r}")
        result.append(tuple(physical_route))

    return tuple(result), state_selection, ready_times, sat_to_route


def _normalise_e2_routes(
    problem,
    e2_routes_by_sat: Mapping[str, Iterable[Sequence[str]]],
    states: Mapping[str, Sequence[ReadyState]],
    state_selection: Mapping[str, int],
    ready_times: Mapping[str, float],
    customer_by_id: Mapping[str, object],
    eps: float,
) -> Tuple[
    Dict[str, Tuple[Tuple[str, ...], ...]],
    Dict[str, str],
    list[Dict[str, Any]],
    Dict[str, list[Dict[str, Any]]],
]:
    sat_ids = set(states)
    normalised: Dict[str, Tuple[Tuple[str, ...], ...]] = {
        sat_id: tuple() for sat_id in sorted(sat_ids)
    }
    assignment: Dict[str, str] = {}
    route_columns: list[Dict[str, Any]] = []
    schedules: Dict[str, list[Dict[str, Any]]] = {}

    for sat_id, raw_routes in (e2_routes_by_sat or {}).items():
        if sat_id not in sat_ids:
            if tuple(raw_routes or ()):
                raise ValueError(f"E2 routes refer to unknown satellite {sat_id!r}")
            continue
        paths = []
        for raw_path in raw_routes or ():
            path = tuple(str(node_id) for node_id in raw_path)
            if len(path) < 3 or path[0] != sat_id or path[-1] != sat_id:
                raise ValueError(f"invalid E2 route for {sat_id!r}: {path!r}")
            for task_id in path[1:-1]:
                if not task_id.endswith("d"):
                    raise ValueError(f"M1/M2 pure-delivery route contains {task_id!r}")
                customer_id = task_id[:-1]
                if customer_id not in customer_by_id:
                    raise ValueError(f"E2 route contains unknown customer {customer_id!r}")
                if customer_id in assignment:
                    raise ValueError(f"customer {customer_id!r} is served more than once")
                assignment[customer_id] = sat_id
            if sat_id not in state_selection:
                raise ValueError(f"E2 route exists at unserved satellite {sat_id!r}")
            state_idx = int(state_selection[sat_id])
            state = states[sat_id][state_idx]
            schedule = forward_m2_route(
                problem,
                sat_id,
                path,
                ready_time=ready_times[sat_id],
                eps=eps,
            )
            if not schedule.feasible:
                raise ValueError(
                    f"E2 route is infeasible at M1-derived ready state {sat_id!r}: "
                    f"{schedule.reason}"
                )
            paths.append(path)
            route_columns.append({
                "sat_id": sat_id,
                "path": path,
                "state_idx": state_idx,
                "ready_time": float(state.ready_time),
                "source": "validated_m1_fixed_assignment_incumbent",
            })
            schedules.setdefault(sat_id, []).append({
                "path": list(path),
                "ready_time": float(schedule.ready_time),
                "arrivals": list(schedule.arrivals),
                "return_time": float(schedule.return_time),
                "distance": float(schedule.distance),
                "load_weight": float(schedule.load_weight),
                "load_volume": float(schedule.load_volume),
            })
        normalised[sat_id] = tuple(paths)

    return normalised, assignment, route_columns, schedules


def _validate_e1_loads_and_due_dates(
    problem,
    e1_routes: Sequence[Sequence[str]],
    assignment: Mapping[str, str],
    ready_times: Mapping[str, float],
    sat_to_e1_route: Mapping[str, int],
    customer_by_id: Mapping[str, object],
    eps: float,
) -> None:
    customers_by_sat: Dict[str, list[str]] = {}
    for customer_id, sat_id in assignment.items():
        customers_by_sat.setdefault(sat_id, []).append(customer_id)

    for route_index, route in enumerate(e1_routes):
        satellites = tuple(route[1:-1])
        load_weight = sum(
            float(customer_by_id[customer_id].weight_del)
            + float(problem.fleet.container_weight)
            for sat_id in satellites
            for customer_id in customers_by_sat.get(sat_id, ())
        )
        load_volume = sum(
            float(problem.fleet.container_volume)
            for sat_id in satellites
            for _customer_id in customers_by_sat.get(sat_id, ())
        )
        if load_weight > float(problem.e1_weight_cap) + eps:
            raise ValueError(
                f"E1 route {route_index} exceeds weight capacity: "
                f"{load_weight:.9f} > {problem.e1_weight_cap:.9f}"
            )
        if load_volume > float(problem.e1_volume_cap) + eps:
            raise ValueError(
                f"E1 route {route_index} exceeds volume capacity: "
                f"{load_volume:.9f} > {problem.e1_volume_cap:.9f}"
            )

    for customer_id, sat_id in assignment.items():
        if sat_id not in sat_to_e1_route:
            raise ValueError(f"customer {customer_id!r} is assigned to an unserved satellite")
        service = float(getattr(problem.nodes[sat_id], "service_time", 0.0))
        arrival_at_satellite = float(ready_times[sat_id]) - service
        due = float(customer_by_id[customer_id].tw_del[1])
        if isfinite(due) and arrival_at_satellite > due + eps:
            raise ValueError(
                f"E1 arrival at {sat_id!r} misses {customer_id!r}'s delivery deadline"
            )


def _e1_route_distance(problem, route: Sequence[str]) -> float:
    return float(sum(
        problem.distance(route[index], route[index + 1])
        for index in range(len(route) - 1)
    ))


def _no_wait_prefix_ready_time(problem, prefix: Sequence[str]) -> float:
    """Return the completion time of a physical depot-to-satellite prefix.

    The arithmetic intentionally mirrors ``enumerate_ready_states`` so that
    an exact-time canonical state can be found without an epsilon merge.
    """

    items = tuple(prefix)
    if len(items) < 2 or items[0] != problem.depot.id:
        raise ValueError(f"invalid no-wait E1 prefix {items!r}")
    elapsed = 0.0
    previous = items[0]
    for sat_id in items[1:]:
        elapsed += float(problem.travel_time(previous, sat_id))
        elapsed += float(getattr(problem.nodes[sat_id], "service_time", 0.0))
        previous = sat_id
    return float(elapsed)


def _physical_satellite_id(node_id: str, sat_ids: set[str]) -> str | None:
    if node_id in sat_ids:
        return node_id
    if node_id.endswith("d") and node_id[:-1] in sat_ids:
        return node_id[:-1]
    return None


def _source_objective(source_info: Mapping[str, Any] | None) -> float | None:
    if not source_info:
        return None
    value = source_info.get("objective")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"source_info objective is not numeric: {value!r}") from exc
