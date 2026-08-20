"""Safe time-expansion primitives for the future exact M6 branch-price path.

Unlike M7, an M6 satellite has two coupled E1 events: delivery completion
releases its E2 vehicles, and E1 pickup cannot occur until every selected E2
route has returned.  Consequently a single M7-style ready time is not a
complete M6 state.  A future certified master must use a delivery-ready time
``h`` together with a pickup-ready time ``p`` (or generate the latter
dynamically) and enforce ``E1_delivery <= h`` and ``E1_pickup >= p``.

This file deliberately does *not* claim to enumerate that global state space.
It supplies the exact per-route forward calculation used by such a master,
including the M6 pickup load increase and a guard for delivery/pickup pairs
split across different E2 routes.  A caller cannot silently treat the latter
as an independently feasible route: it receives an explicit external
precedence dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping, Sequence, Tuple


EPS = 1e-9


@dataclass(frozen=True)
class M6RouteSchedule:
    """Earliest route schedule at a fixed E1 delivery-ready time.

    ``feasible`` means that all route-local capacity, time-window, and
    delivery-before-pickup requirements are satisfied.  It may still contain
    ``external_delivery_dependencies``: those pickups require a delivery on a
    different E2 route, so a future master must add/price the corresponding
    cross-route precedence before certifying a global solution.
    """

    feasible: bool
    reason: str | None
    sat_id: str
    path: Tuple[str, ...]
    ready_time: float
    arrivals: Tuple[Tuple[str, float], ...]
    return_time: float | None
    distance: float | None
    initial_weight: float
    initial_volume: float
    external_delivery_dependencies: Tuple[str, ...]


@dataclass(frozen=True)
class M6SatelliteSchedule:
    """A validated set of E2 routes between a satellite's two E1 events."""

    feasible: bool
    reason: str | None
    sat_id: str
    ready_time: float
    pickup_ready_time: float
    task_arrivals: Tuple[Tuple[str, float], ...]
    route_return_times: Tuple[float, ...]


@dataclass(frozen=True)
class M6SatelliteProgram:
    """One validated, state-scoped E2 program for a satellite.

    This is the correct future master-column granularity for M6 whenever a
    customer's delivery and pickup can be placed on different E2 vehicles.
    A program packages *all* selected routes at that satellite, so the master
    never loses their cross-route delivery-to-pickup precedence.
    """

    sat_id: str
    ready_time: float
    pickup_ready_time: float
    routes: Tuple[Tuple[str, ...], ...]
    customers: Tuple[str, ...]
    cost: float
    schedule: M6SatelliteSchedule


@dataclass(frozen=True)
class M6E1ProgramWitness:
    """Validated E1 schedule linked to selected M6 satellite programs."""

    feasible: bool
    reason: str | None
    arrivals: Tuple[Tuple[str, float], ...]


def tighten_m6_program_pickup_state(
    problem,
    program: M6SatelliteProgram,
    *,
    eps: float = EPS,
) -> M6SatelliteProgram | None:
    """Return a valid same-route program with its earliest pickup state.

    A program may have been inserted at a deliberately loose pickup-ready
    time ``p`` so an E1 route can wait there.  Its E2 routes often all return
    earlier.  The tightened ``(h, p')`` state has identical customer service
    and route cost but gives the global E1 master an earlier pickup option.

    This is a safe *state enrichment* primitive, not a complete state
    generator: it only discovers ``p'`` from a program that is already known.
    It returns ``None`` when the state is already tight.
    """

    if not program.schedule.feasible:
        raise ValueError("cannot tighten an infeasible M6 program")
    earliest_p = max(program.schedule.route_return_times, default=program.ready_time)
    if earliest_p >= program.pickup_ready_time - eps:
        return None
    return make_m6_satellite_program(
        problem,
        program.sat_id,
        program.routes,
        ready_time=program.ready_time,
        pickup_ready_time=earliest_p,
        eps=eps,
    )


def forward_m6_route(
    problem,
    sat_id: str,
    path: Sequence[str],
    *,
    ready_time: float,
    external_delivery_times: Mapping[str, float] | None = None,
    eps: float = EPS,
) -> M6RouteSchedule:
    """Forward-schedule one M6 E2 route from satellite ``sat_id``.

    The vehicle initially carries exactly the deliveries on this route.  A
    delivery reduces its load by ``delivery_weight + container_weight``;
    a pickup increases it by one container.  Pickup service is allowed only
    after the same customer's delivery is complete.  If that delivery is on
    another route, the caller must supply its absolute completion time through
    ``external_delivery_times``.  Omitting it leaves an explicit dependency
    in the returned schedule rather than asserting unconditional feasibility.
    """

    items = tuple(path)
    if len(items) < 3:
        return _bad(sat_id, items, ready_time, "route must contain a task")
    if items[0] != sat_id or items[-1] != sat_id:
        return _bad(sat_id, items, ready_time, "route endpoints do not match satellite")
    if ready_time < -eps:
        return _bad(sat_id, items, ready_time, "negative delivery-ready time")

    customers = {customer.id: customer for customer in problem.customers}
    task_ids = items[1:-1]
    seen_tasks: set[str] = set()
    deliveries_on_route: set[str] = set()
    initial_weight = 0.0
    initial_volume = 0.0
    container_weight = float(problem.fleet.container_weight)
    container_volume = float(problem.fleet.container_volume)

    for task_id in task_ids:
        if len(task_id) < 2 or task_id[-1] not in {"d", "p"}:
            return _bad(sat_id, items, ready_time, f"unknown task kind {task_id!r}")
        customer_id = task_id[:-1]
        if customer_id not in customers:
            return _bad(sat_id, items, ready_time, f"unknown customer {customer_id!r}")
        if task_id in seen_tasks:
            return _bad(sat_id, items, ready_time, f"duplicate task {task_id!r}")
        seen_tasks.add(task_id)
        if task_id.endswith("d"):
            deliveries_on_route.add(customer_id)
            initial_weight += float(customers[customer_id].weight_del) + container_weight
            initial_volume += container_volume

    if initial_weight > float(problem.e2_weight_cap) + eps:
        return _bad(sat_id, items, ready_time, "initial E2 weight capacity exceeded", initial_weight, initial_volume)
    if initial_volume > float(problem.e2_volume_cap) + eps:
        return _bad(sat_id, items, ready_time, "initial E2 volume capacity exceeded", initial_weight, initial_volume)

    supplied_external = {
        str(customer_id): float(time_value)
        for customer_id, time_value in (external_delivery_times or {}).items()
    }
    completed_delivery: dict[str, float] = {}
    unresolved_external: set[str] = set()
    arrivals: list[tuple[str, float]] = []
    distance = 0.0
    current_time = max(0.0, float(ready_time))
    current_location = sat_id
    current_weight = initial_weight
    current_volume = initial_volume

    for task_id in task_ids:
        customer_id, kind = task_id[:-1], task_id[-1]
        customer = customers[customer_id]
        distance += float(problem.distance(current_location, customer_id))
        arrival = current_time + float(problem.travel_time(current_location, customer_id))
        lower, upper = customer.tw_del if kind == "d" else customer.tw_pick
        arrival = max(arrival, float(lower))

        if kind == "p":
            delivery_done = completed_delivery.get(customer_id)
            if delivery_done is None and customer_id in deliveries_on_route:
                # The matching delivery occurs later in this same route.
                # It cannot be supplied by another route, so accepting an
                # external dependency here would silently violate the M6
                # delivery-before-pickup requirement.
                return _bad(
                    sat_id, items, ready_time,
                    f"pickup precedes same-route delivery for {customer_id!r}",
                    initial_weight, initial_volume, tuple(sorted(unresolved_external)),
                )
            if delivery_done is None:
                delivery_done = supplied_external.get(customer_id)
            if delivery_done is None:
                unresolved_external.add(customer_id)
            else:
                # A pickup starts only after the delivery service completes.
                arrival = max(arrival, delivery_done)

        if isfinite(float(upper)) and arrival > float(upper) + eps:
            return _bad(
                sat_id, items, ready_time,
                f"{kind} time window violated at {customer_id!r}",
                initial_weight, initial_volume, tuple(sorted(unresolved_external)),
            )

        arrivals.append((task_id, arrival))
        completion = arrival + float(customer.service_time)
        if kind == "d":
            current_weight -= float(customer.weight_del) + container_weight
            current_volume -= container_volume
            completed_delivery[customer_id] = completion
        else:
            current_weight += container_weight
            current_volume += container_volume

        if current_weight < -eps or current_weight > float(problem.e2_weight_cap) + eps:
            return _bad(
                sat_id, items, ready_time, f"E2 weight capacity violated after {task_id!r}",
                initial_weight, initial_volume, tuple(sorted(unresolved_external)),
            )
        if current_volume < -eps or current_volume > float(problem.e2_volume_cap) + eps:
            return _bad(
                sat_id, items, ready_time, f"E2 volume capacity violated after {task_id!r}",
                initial_weight, initial_volume, tuple(sorted(unresolved_external)),
            )
        current_time = completion
        current_location = customer_id

    distance += float(problem.distance(current_location, sat_id))
    return M6RouteSchedule(
        feasible=True,
        reason=None,
        sat_id=sat_id,
        path=items,
        ready_time=float(ready_time),
        arrivals=tuple(arrivals),
        return_time=current_time + float(problem.travel_time(current_location, sat_id)),
        distance=distance,
        initial_weight=initial_weight,
        initial_volume=initial_volume,
        external_delivery_dependencies=tuple(sorted(unresolved_external)),
    )


def validate_m6_satellite_schedule(
    problem,
    sat_id: str,
    routes: Sequence[Sequence[str]],
    *,
    ready_time: float,
    pickup_ready_time: float,
    eps: float = EPS,
) -> M6SatelliteSchedule:
    """Validate all E2 routes at one satellite's ``(h, p)`` state.

    Each route starts no earlier than ``h`` and must return by ``p``.  The
    calculation jointly propagates route arcs, customer delivery-to-pickup
    arcs (including pairs split across vehicles), and task time windows.  It
    therefore provides a safe witness check for a future M6 time-expanded
    master; it is intentionally not a route-column generator or proof by
    itself.
    """

    if pickup_ready_time + eps < ready_time:
        return _sat_bad(sat_id, ready_time, pickup_ready_time, "pickup state precedes delivery state")
    customers = {customer.id: customer for customer in problem.customers}
    task_routes: dict[str, int] = {}
    route_tasks: list[tuple[str, ...]] = []
    route_checks: list[M6RouteSchedule] = []

    # Local capacity and same-route-order checks.  External delivery times
    # are set only to a harmless lower bound; the joint propagation below
    # supplies the actual cross-route delivery precedences.
    for route_idx, raw_path in enumerate(routes):
        path = tuple(raw_path)
        check = forward_m6_route(
            problem, sat_id, path, ready_time=ready_time,
            external_delivery_times={customer_id: 0.0 for customer_id in customers}, eps=eps,
        )
        if not check.feasible:
            return _sat_bad(sat_id, ready_time, pickup_ready_time, check.reason or "route infeasible")
        tasks = tuple(path[1:-1])
        for task_id in tasks:
            if task_id in task_routes:
                return _sat_bad(sat_id, ready_time, pickup_ready_time, f"task {task_id!r} appears in two routes")
            task_routes[task_id] = route_idx
        route_tasks.append(tasks)
        route_checks.append(check)

    # A complete satellite witness must include both events for every served
    # customer.  This is where a future master will connect assignment/cover
    # rows to the two-state route schedule.
    served_customers = {task_id[:-1] for task_id in task_routes}
    for customer_id in served_customers:
        if f"{customer_id}d" not in task_routes or f"{customer_id}p" not in task_routes:
            return _sat_bad(sat_id, ready_time, pickup_ready_time, f"incomplete d/p pair for {customer_id!r}")

    # Lower-bound arcs a -> b with b_time >= a_time + lag.  Repeated relaxation
    # computes the earliest feasible schedule and detects positive cycles.
    # A cycle can arise when cross-route pickup/delivery order conflicts with
    # the two route orders, so it must not be silently accepted.
    lower: dict[str, float] = {}
    arcs: list[tuple[str | None, str, float]] = []
    for route_idx, tasks in enumerate(route_tasks):
        if not tasks:
            return _sat_bad(sat_id, ready_time, pickup_ready_time, "empty E2 route")
        first = tasks[0]
        arcs.append((None, first, float(problem.travel_time(sat_id, first[:-1]))))
        for previous, current in zip(tasks, tasks[1:]):
            arcs.append((
                previous, current,
                float(getattr(customers[previous[:-1]], "service_time", 0.0))
                + float(problem.travel_time(previous[:-1], current[:-1])),
            ))
        for task_id in tasks:
            customer = customers[task_id[:-1]]
            tw = customer.tw_del if task_id.endswith("d") else customer.tw_pick
            lower[task_id] = float(tw[0])
    for customer_id in served_customers:
        arcs.append((
            f"{customer_id}d", f"{customer_id}p",
            float(getattr(customers[customer_id], "service_time", 0.0)),
        ))

    task_count = len(lower)
    for _ in range(task_count):
        changed = False
        for source, target, lag in arcs:
            candidate = ready_time + lag if source is None else lower[source] + lag
            if candidate > lower[target] + eps:
                lower[target] = candidate
                changed = True
        if not changed:
            break
    else:
        return _sat_bad(sat_id, ready_time, pickup_ready_time, "positive timing cycle between selected routes")

    for task_id, arrival in lower.items():
        customer = customers[task_id[:-1]]
        _, upper = customer.tw_del if task_id.endswith("d") else customer.tw_pick
        if isfinite(float(upper)) and arrival > float(upper) + eps:
            return _sat_bad(sat_id, ready_time, pickup_ready_time, f"time window violated at {task_id!r}")

    returns: list[float] = []
    for tasks in route_tasks:
        last = tasks[-1]
        returns.append(
            lower[last]
            + float(getattr(customers[last[:-1]], "service_time", 0.0))
            + float(problem.travel_time(last[:-1], sat_id))
        )
    if any(value > pickup_ready_time + eps for value in returns):
        return _sat_bad(sat_id, ready_time, pickup_ready_time, "an E2 route returns after pickup-ready state")

    return M6SatelliteSchedule(
        feasible=True,
        reason=None,
        sat_id=sat_id,
        ready_time=float(ready_time),
        pickup_ready_time=float(pickup_ready_time),
        task_arrivals=tuple(sorted(lower.items())),
        route_return_times=tuple(returns),
    )


def make_m6_satellite_program(
    problem,
    sat_id: str,
    routes: Sequence[Sequence[str]],
    *,
    ready_time: float,
    pickup_ready_time: float | None = None,
    eps: float = EPS,
) -> M6SatelliteProgram:
    """Build one validated M6 satellite-program column.

    If ``pickup_ready_time`` is omitted, the program uses the earliest value
    implied by the jointly scheduled E2 route returns.  Supplying a larger
    value is valid (the E1 vehicle may wait); a smaller one is rejected.
    The function is intentionally a witness constructor, not a complete
    program generator or an optimality certificate.
    """

    route_tuple = tuple(tuple(route) for route in routes)
    # Find the earliest jointly feasible timing first.  A sufficiently broad
    # provisional p lets the validator calculate all E2 return events.
    provisional_p = float(pickup_ready_time) if pickup_ready_time is not None else float("inf")
    schedule = validate_m6_satellite_schedule(
        problem, sat_id, route_tuple,
        ready_time=ready_time, pickup_ready_time=provisional_p, eps=eps,
    )
    if not schedule.feasible:
        raise ValueError(f"invalid M6 satellite program: {schedule.reason}")
    earliest_p = max(schedule.route_return_times, default=float(ready_time))
    selected_p = earliest_p if pickup_ready_time is None else float(pickup_ready_time)
    if selected_p + eps < earliest_p:
        raise ValueError("pickup_ready_time precedes an E2 route return")
    # Revalidate with the exact selected p so callers receive an auditable
    # state witness rather than a schedule checked against infinity.
    schedule = validate_m6_satellite_schedule(
        problem, sat_id, route_tuple,
        ready_time=ready_time, pickup_ready_time=selected_p, eps=eps,
    )
    if not schedule.feasible:
        raise ValueError(f"invalid M6 satellite program: {schedule.reason}")

    customers = tuple(sorted({task_id[:-1] for route in route_tuple for task_id in route[1:-1]}))
    cost = sum(
        sum(float(problem.distance(left[:-1] if left.endswith(("d", "p")) else left,
                                   right[:-1] if right.endswith(("d", "p")) else right))
            for left, right in zip(route, route[1:]))
        for route in route_tuple
    )
    return M6SatelliteProgram(
        sat_id=sat_id,
        ready_time=float(ready_time),
        pickup_ready_time=selected_p,
        routes=route_tuple,
        customers=customers,
        cost=cost,
        schedule=schedule,
    )


def validate_m6_e1_program_witness(
    problem,
    e1_routes: Sequence[Sequence[str]],
    programs_by_satellite: Mapping[str, M6SatelliteProgram],
    *,
    eps: float = EPS,
) -> M6E1ProgramWitness:
    """Validate E1 delivery/pickup routes against selected ``(h,p)`` programs.

    E1 may wait at a pickup node until the selected program's E2 routes have
    returned.  It may not delay a delivery node beyond the tightest delivery
    deadline among that program's customers.  The load scan exactly mirrors
    M6: E1 starts with all assigned deliveries, unloads at ``S*d``, and gains
    one container per assigned customer at ``S*p``.
    """

    depot_id = problem.depot.id
    sat_ids = {sat.id for sat in problem.satellites}
    customers = {customer.id: customer for customer in problem.customers}
    seen_events: set[str] = set()
    arrivals: list[tuple[str, float]] = []

    for raw_route in e1_routes:
        route = tuple(raw_route)
        if len(route) < 2 or route[0] != depot_id or route[-1] != depot_id:
            return _e1_bad("invalid E1 route endpoints")
        events = route[1:-1]
        route_programs: dict[str, M6SatelliteProgram] = {}
        delivery_sats: set[str] = set()
        for event in events:
            if len(event) < 2 or event[-1] not in {"d", "p"} or event[:-1] not in sat_ids:
                return _e1_bad(f"invalid E1 event {event!r}")
            if event in seen_events:
                return _e1_bad(f"duplicate E1 event {event!r}")
            seen_events.add(event)
            program = programs_by_satellite.get(event[:-1])
            if program is None:
                return _e1_bad(f"E1 visits satellite without selected program {event[:-1]!r}")
            route_programs[event[:-1]] = program
            if event.endswith("d"):
                delivery_sats.add(event[:-1])

        load_weight = sum(
            sum(float(customers[customer_id].weight_del) + float(problem.fleet.container_weight)
                for customer_id in program.customers)
            for sat_id, program in route_programs.items() if sat_id in delivery_sats
        )
        load_volume = sum(
            len(program.customers) * float(problem.fleet.container_volume)
            for sat_id, program in route_programs.items() if sat_id in delivery_sats
        )
        if load_weight > float(problem.e1_weight_cap) + eps or load_volume > float(problem.e1_volume_cap) + eps:
            return _e1_bad("E1 initial capacity exceeded")

        previous = depot_id
        current_time = 0.0
        for event in events:
            sat_id, kind = event[:-1], event[-1]
            program = programs_by_satellite[sat_id]
            arrival = current_time + float(problem.travel_time(previous, sat_id))
            if kind == "d":
                completion = arrival + float(getattr(problem.nodes[sat_id], "service_time", 0.0))
                if completion > program.ready_time + eps:
                    return _e1_bad(f"E1 delivery at {sat_id!r} misses program ready time")
                due = min((float(customers[c].tw_del[1]) for c in program.customers), default=float("inf"))
                if isfinite(due) and arrival > due + eps:
                    return _e1_bad(f"E1 delivery at {sat_id!r} misses customer deadline")
                load_weight -= sum(float(customers[c].weight_del) + float(problem.fleet.container_weight) for c in program.customers)
                load_volume -= len(program.customers) * float(problem.fleet.container_volume)
            else:
                arrival = max(arrival, program.pickup_ready_time)
                completion = arrival + float(getattr(problem.nodes[sat_id], "service_time", 0.0))
                load_weight += len(program.customers) * float(problem.fleet.container_weight)
                load_volume += len(program.customers) * float(problem.fleet.container_volume)
            if load_weight < -eps or load_weight > float(problem.e1_weight_cap) + eps:
                return _e1_bad(f"E1 weight capacity violated at {event!r}")
            if load_volume < -eps or load_volume > float(problem.e1_volume_cap) + eps:
                return _e1_bad(f"E1 volume capacity violated at {event!r}")
            arrivals.append((event, arrival))
            current_time = completion
            previous = sat_id

    for sat_id in programs_by_satellite:
        if f"{sat_id}d" not in seen_events or f"{sat_id}p" not in seen_events:
            return _e1_bad(f"selected program at {sat_id!r} lacks E1 delivery/pickup")
    return M6E1ProgramWitness(True, None, tuple(arrivals))


def _bad(
    sat_id: str,
    path: Tuple[str, ...],
    ready_time: float,
    reason: str,
    initial_weight: float = 0.0,
    initial_volume: float = 0.0,
    external: Tuple[str, ...] = (),
) -> M6RouteSchedule:
    return M6RouteSchedule(
        feasible=False,
        reason=reason,
        sat_id=sat_id,
        path=path,
        ready_time=float(ready_time),
        arrivals=(),
        return_time=None,
        distance=None,
        initial_weight=initial_weight,
        initial_volume=initial_volume,
        external_delivery_dependencies=external,
    )


def _sat_bad(sat_id: str, ready_time: float, pickup_ready_time: float, reason: str) -> M6SatelliteSchedule:
    return M6SatelliteSchedule(
        feasible=False,
        reason=reason,
        sat_id=sat_id,
        ready_time=float(ready_time),
        pickup_ready_time=float(pickup_ready_time),
        task_arrivals=(),
        route_return_times=(),
    )


def _e1_bad(reason: str) -> M6E1ProgramWitness:
    return M6E1ProgramWitness(False, reason, ())
