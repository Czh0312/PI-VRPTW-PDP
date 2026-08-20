"""Certified time-expansion primitives for Model 7.

This module intentionally contains no restricted route-pool solve.  It holds
the two pieces needed by a future exact branch-price implementation:

* a finite set of satellite-ready states induced by *no-wait* E1 prefixes;
* an exact forward feasibility/schedule calculation for an E2 delivery route
  at one such ready time.

Why the finite state set is safe for Model 7
--------------------------------------------
E1 has no lower time windows and its objective contains distance only.  Hence
any feasible M7 solution can be changed to make every E1 truck leave and move
without waiting.  This can only make each satellite available earlier and
cannot hurt an E2 delivery route (an E2 vehicle can wait at a customer).
The availability time at a satellite is therefore the completion time of a
simple depot-to-satellite prefix.  With ``|F|`` satellites there are exactly
``sum(P(|F|-1, q), q=0..|F|-1)`` such prefixes per satellite before duplicate
travel times are removed.  The target 50-customer instances have four
satellites, so this is at most 16 states per satellite.

The future time-expanded master should choose one ready state ``h`` for every
served satellite and use route columns ``lambda[s, h, r]`` only when
``forward_m7_route(..., ready_time=h)`` is feasible.  That makes route timing
a column attribute rather than a newly-created master constraint, which is
what permits complete reduced-cost pricing.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from math import isfinite
from typing import Dict, Iterable, Mapping, Sequence, Tuple


EPS = 1e-9


@dataclass(frozen=True)
class ReadyState:
    """One no-wait E1 completion state for a satellite.

    ``prefix`` uses physical node IDs: ``(depot, ..., satellite)``.  The
    ``ready_time`` includes service at the final satellite, because an E2
    vehicle may depart only after that service completes.
    """

    sat_id: str
    ready_time: float
    prefix: Tuple[str, ...]


@dataclass(frozen=True)
class M7RouteSchedule:
    """A deterministic earliest feasible schedule of one M7 E2 route."""

    feasible: bool
    reason: str | None
    sat_id: str
    path: Tuple[str, ...]
    ready_time: float
    arrivals: Tuple[Tuple[str, float], ...]
    return_time: float | None
    distance: float | None
    load_weight: float
    load_volume: float


def enumerate_ready_states(problem) -> Dict[str, Tuple[ReadyState, ...]]:
    """Enumerate all no-wait E1 prefix completion states.

    The enumeration deliberately keeps duplicate numerical times if they stem
    from different prefixes.  Removing an almost-equal floating-point value
    would be a numerical modelling decision; retaining it is harmless and
    preserves the simple completeness argument exactly.
    """

    depot_id = problem.depot.id
    sat_ids = tuple(s.id for s in problem.satellites)
    service = {
        s.id: float(getattr(s, "service_time", 0.0))
        for s in problem.satellites
    }
    states: Dict[str, list[ReadyState]] = {s: [] for s in sat_ids}

    # Every nonempty ordered subset of satellites is a possible E1 prefix.
    # Its last node is the satellite whose availability it realizes.
    for length in range(1, len(sat_ids) + 1):
        for suffix in permutations(sat_ids, length):
            prefix = (depot_id,) + suffix
            elapsed = 0.0
            previous = depot_id
            for sat_id in suffix:
                elapsed += float(problem.travel_time(previous, sat_id))
                elapsed += service[sat_id]
                previous = sat_id
            states[suffix[-1]].append(
                ReadyState(
                    sat_id=suffix[-1],
                    ready_time=elapsed,
                    prefix=prefix,
                )
            )

    return {
        sat_id: tuple(sorted(items, key=lambda item: (item.ready_time, item.prefix)))
        for sat_id, items in states.items()
    }


def canonicalize_ready_states(
    ready_states: Mapping[str, Sequence[ReadyState]],
) -> Dict[str, Tuple[ReadyState, ...]]:
    """Collapse states with exactly identical ready times, per satellite.

    This is a *fresh-run* state-space reduction for the M7 time-expanded
    master.  Its correctness does not rely on a geometric symmetry of the
    source prefixes: the global E1 arc-flow stays in the master, while every
    E2-column and E1-ready constraint uses a :class:`ReadyState` only through
    ``(sat_id, ready_time)``.  Therefore replacing one state by another at
    the same exact ``ready_time`` leaves every coefficient and feasibility
    condition unchanged.  ``prefix`` is retained solely as a stable,
    lexicographically first diagnostic representative.

    Deliberately, this function groups by *exact* Python float equality.  It
    must not use an epsilon or rounded key: two almost-equal completion times
    can differ at a tight delivery deadline, and merging them would not have
    the above exact-equivalence proof.

    The returned state indices differ from :func:`enumerate_ready_states`.
    A route-pool checkpoint with state-scoped columns must consequently carry
    and reuse the same state-family mode; callers must not mix the two.
    """

    result: Dict[str, Tuple[ReadyState, ...]] = {}
    for sat_id, supplied in ready_states.items():
        representatives: Dict[float, ReadyState] = {}
        for state in supplied:
            if state.sat_id != sat_id:
                raise ValueError(
                    f"ready state {state!r} belongs to {state.sat_id!r}, not {sat_id!r}"
                )
            time_key = float(state.ready_time)
            current = representatives.get(time_key)
            if current is None or tuple(state.prefix) < tuple(current.prefix):
                representatives[time_key] = state
        if not representatives:
            raise ValueError(f"no ready states supplied for satellite {sat_id!r}")
        result[sat_id] = tuple(
            sorted(
                representatives.values(),
                key=lambda state: (float(state.ready_time), tuple(state.prefix)),
            )
        )
    return result


def enumerate_canonical_ready_states(problem) -> Dict[str, Tuple[ReadyState, ...]]:
    """Enumerate the complete M7 state family with exact-time duplicates merged.

    The default :func:`enumerate_ready_states` intentionally preserves every
    source prefix for diagnostics and legacy state-index compatibility.  Use
    this opt-in helper only for a new run (or a checkpoint explicitly marked
    as canonical); it removes only the equivalent exact-time duplicates
    described in :func:`canonicalize_ready_states`.
    """

    return canonicalize_ready_states(enumerate_ready_states(problem))


def ready_state_count(num_satellites: int) -> int:
    """Return the number of ordered E1 prefixes ending at one satellite."""

    if num_satellites < 0:
        raise ValueError("num_satellites must be non-negative")
    total = 0
    term = 1  # P(n-1, 0)
    total += term
    for q in range(1, num_satellites):
        term *= num_satellites - q
        total += term
    return total


def forward_m7_route(
    problem,
    sat_id: str,
    path: Sequence[str],
    *,
    ready_time: float,
    eps: float = EPS,
) -> M7RouteSchedule:
    """Validate and earliest-schedule a pure-delivery E2 route.

    Waiting is allowed at customers.  Thus starting the route at
    ``ready_time`` and propagating each task to ``max(arrival, ready)`` is
    feasible iff the path can be run after that satellite becomes available.
    This matches Model 7's timing semantics and does not rely on a Big-M
    relaxation.
    """

    path_tuple = tuple(path)
    if len(path_tuple) < 3:
        return _infeasible(sat_id, path_tuple, ready_time, "route must contain a customer")
    if path_tuple[0] != sat_id or path_tuple[-1] != sat_id:
        return _infeasible(sat_id, path_tuple, ready_time, "route endpoints do not match satellite")
    if ready_time < -eps:
        return _infeasible(sat_id, path_tuple, ready_time, "negative ready time")

    customers = {customer.id: customer for customer in problem.customers}
    container_weight = float(problem.fleet.container_weight)
    container_volume = float(problem.fleet.container_volume)

    task_ids = path_tuple[1:-1]
    seen_customers = set()
    load_weight = 0.0
    load_volume = 0.0
    for task_id in task_ids:
        if not task_id.endswith("d"):
            return _infeasible(sat_id, path_tuple, ready_time, f"non-delivery task {task_id!r}")
        customer_id = task_id[:-1]
        customer = customers.get(customer_id)
        if customer is None:
            return _infeasible(sat_id, path_tuple, ready_time, f"unknown customer {customer_id!r}")
        if customer_id in seen_customers:
            return _infeasible(sat_id, path_tuple, ready_time, f"duplicate customer {customer_id!r}")
        seen_customers.add(customer_id)
        load_weight += float(customer.weight_del) + container_weight
        load_volume += container_volume

    if load_weight > float(problem.e2_weight_cap) + eps:
        return _infeasible(
            sat_id, path_tuple, ready_time, "E2 weight capacity exceeded", load_weight, load_volume
        )
    if load_volume > float(problem.e2_volume_cap) + eps:
        return _infeasible(
            sat_id, path_tuple, ready_time, "E2 volume capacity exceeded", load_weight, load_volume
        )

    current_location = sat_id
    current_time = max(0.0, float(ready_time))
    distance = 0.0
    arrivals: list[Tuple[str, float]] = []
    for task_id in task_ids:
        customer = customers[task_id[:-1]]
        travel_time = float(problem.travel_time(current_location, customer.id))
        distance += float(problem.distance(current_location, customer.id))
        arrival = max(current_time + travel_time, float(customer.tw_del[0]))
        due = float(customer.tw_del[1])
        if isfinite(due) and arrival > due + eps:
            return _infeasible(
                sat_id,
                path_tuple,
                ready_time,
                f"delivery due time violated at {customer.id!r}",
                load_weight,
                load_volume,
            )
        arrivals.append((task_id, arrival))
        current_time = arrival + float(customer.service_time)
        current_location = customer.id

    distance += float(problem.distance(current_location, sat_id))
    return_time = current_time + float(problem.travel_time(current_location, sat_id))
    return M7RouteSchedule(
        feasible=True,
        reason=None,
        sat_id=sat_id,
        path=path_tuple,
        ready_time=float(ready_time),
        arrivals=tuple(arrivals),
        return_time=return_time,
        distance=distance,
        load_weight=load_weight,
        load_volume=load_volume,
    )


def e1_ready_times_from_routes(
    problem,
    e1_routes: Iterable[Sequence[str]],
    *,
    eps: float = EPS,
) -> Dict[str, float]:
    """Return no-wait satellite completion times implied by E1 route paths.

    ``extract_routes_e1`` returns logical satellite delivery IDs such as
    ``S0d``.  Physical satellite IDs are also accepted to make this helper
    useful for ALNS routes.  A satellite may appear on at most one route.
    """

    depot_id = problem.depot.id
    sat_ids = {sat.id for sat in problem.satellites}
    service = {
        sat.id: float(getattr(sat, "service_time", 0.0))
        for sat in problem.satellites
    }
    result: Dict[str, float] = {}

    for route in e1_routes:
        items = tuple(route)
        if len(items) < 3 or items[0] != depot_id or items[-1] != depot_id:
            raise ValueError(f"invalid E1 route endpoints: {items!r}")
        elapsed = 0.0
        previous = depot_id
        for node_id in items[1:-1]:
            sat_id = _physical_satellite_id(node_id, sat_ids)
            if sat_id is None:
                raise ValueError(f"E1 route contains non-delivery satellite node {node_id!r}")
            if sat_id in result:
                raise ValueError(f"satellite {sat_id!r} appears in more than one E1 route")
            elapsed += float(problem.travel_time(previous, sat_id))
            elapsed += service[sat_id]
            result[sat_id] = elapsed
            previous = sat_id
        # The return leg does not affect any satellite's ready time.

    return result


def validate_m7_time_expansion_witness(
    problem,
    e1_routes: Iterable[Sequence[str]],
    e2_routes_by_sat: Mapping[str, Iterable[Sequence[str]]],
    *,
    eps: float = EPS,
) -> Dict[str, Tuple[M7RouteSchedule, ...]]:
    """Validate the E1→E2 timing part of a concrete M7 incumbent.

    The caller still needs to validate assignment, coverage, and E1 loads.
    This helper proves the key time-expansion invariant: each selected E2
    route remains feasible when all routes at its satellite depart as soon as
    E1 service there has completed.
    """

    ready_times = e1_ready_times_from_routes(problem, e1_routes, eps=eps)
    schedules: Dict[str, Tuple[M7RouteSchedule, ...]] = {}
    for sat_id, routes in e2_routes_by_sat.items():
        route_items = tuple(routes)
        if not route_items:
            continue
        if sat_id not in ready_times:
            raise ValueError(f"E2 routes exist at unserved satellite {sat_id!r}")
        scheduled = tuple(
            forward_m7_route(
                problem,
                sat_id,
                route,
                ready_time=ready_times[sat_id],
                eps=eps,
            )
            for route in route_items
        )
        failed = next((item for item in scheduled if not item.feasible), None)
        if failed is not None:
            raise ValueError(
                f"E2 route is infeasible after E1 readiness at {sat_id!r}: {failed.reason}"
            )
        schedules[sat_id] = scheduled
    return schedules


def _physical_satellite_id(node_id: str, sat_ids: set[str]) -> str | None:
    if node_id in sat_ids:
        return node_id
    if node_id.endswith("d") and node_id[:-1] in sat_ids:
        return node_id[:-1]
    return None


def _infeasible(
    sat_id: str,
    path: Tuple[str, ...],
    ready_time: float,
    reason: str,
    load_weight: float = 0.0,
    load_volume: float = 0.0,
) -> M7RouteSchedule:
    return M7RouteSchedule(
        feasible=False,
        reason=reason,
        sat_id=sat_id,
        path=path,
        ready_time=float(ready_time),
        arrivals=(),
        return_time=None,
        distance=None,
        load_weight=load_weight,
        load_volume=load_volume,
    )
