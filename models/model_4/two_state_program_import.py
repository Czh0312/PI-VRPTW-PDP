"""Import a feasible M4 route solution as validated two-state program seeds.

This bridges the certified arc-flow model (or a separately validated ALNS
incumbent) to the M4 two-state branch-price prototype.  It reconstructs the
earliest joint E1/E2 temporal schedule from the fixed routes, then emits one
``M4SatelliteProgram`` per used satellite with its exact delivery-ready ``h``
and pickup-ready ``p`` state.

The importer is deliberately a feasibility adapter, not an optimality claim.
Its output is safe as a restricted-master seed only after all capacity, time
window, precedence, and E1/E2 coupling checks pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping, Sequence

from models.model_4.certified_time_expansion import (
    M4E1ProgramWitness,
    M4SatelliteProgram,
    make_m4_satellite_program,
    validate_m4_e1_program_witness,
)


EPS = 1e-8


@dataclass(frozen=True)
class M4ImportedProgramSeed:
    """A route-derived seed plus its independently validated E1 witness."""

    programs: tuple[M4SatelliteProgram, ...]
    e1_witness: M4E1ProgramWitness
    assignment: Mapping[str, str]


def import_m4_routes_as_two_state_programs(
    problem,
    e1_routes: Sequence[Sequence[str]],
    e2_routes_by_sat: Mapping[str, Sequence[Sequence[str]]],
    *,
    eps: float = EPS,
) -> M4ImportedProgramSeed:
    """Convert fixed feasible-looking M4 routes into auditable program seeds.

    The routine does not trust the input schedule.  It builds a temporal
    precedence graph whose arcs encode E1 route order, E1 delivery→E2 start,
    E2 route order, customer delivery→pickup, and E2 return→E1 pickup.  An
    earliest-feasible relaxation produces the state values.  Positive cycles
    and time-window violations reject the input rather than emitting a seed.
    """

    depot_id = problem.depot.id
    sat_ids = {sat.id for sat in problem.satellites}
    customers = {customer.id: customer for customer in problem.customers}
    normalized_e1 = tuple(tuple(route) for route in e1_routes if route)
    normalized_e2 = {
        str(sat_id): tuple(tuple(route) for route in routes if route)
        for sat_id, routes in e2_routes_by_sat.items()
    }
    if any(sat_id not in sat_ids for sat_id in normalized_e2):
        raise ValueError("E2 routes reference an unknown satellite")

    lower: dict[str, float] = {}
    arcs: list[tuple[str | None, str, float]] = []
    seen_e1_events: set[str] = set()
    for route in normalized_e1:
        if len(route) < 2 or route[0] != depot_id or route[-1] != depot_id:
            raise ValueError("invalid E1 route endpoints")
        previous: str | None = None
        for event in route[1:-1]:
            if len(event) < 2 or event[-1] not in {"d", "p"} or event[:-1] not in sat_ids:
                raise ValueError(f"invalid E1 event {event!r}")
            if event in seen_e1_events:
                raise ValueError(f"duplicate E1 event {event!r}")
            seen_e1_events.add(event)
            lower[event] = 0.0
            physical_previous = depot_id if previous is None else previous[:-1]
            service_previous = 0.0 if previous is None else float(
                getattr(problem.nodes[previous[:-1]], "service_time", 0.0)
            )
            arcs.append((
                previous,
                event,
                service_previous + float(problem.travel_time(physical_previous, event[:-1])),
            ))
            previous = event

    task_owner: dict[str, str] = {}
    used_satellites: list[str] = []
    routes_by_used_sat: dict[str, tuple[tuple[str, ...], ...]] = {}
    for sat_id, routes in normalized_e2.items():
        if not routes:
            continue
        if f"{sat_id}d" not in seen_e1_events or f"{sat_id}p" not in seen_e1_events:
            raise ValueError(f"E2 routes at {sat_id!r} lack matching E1 delivery/pickup")
        used_satellites.append(sat_id)
        routes_by_used_sat[sat_id] = routes
        for route in routes:
            if len(route) < 3 or route[0] != sat_id or route[-1] != sat_id:
                raise ValueError(f"invalid E2 route endpoints at {sat_id!r}")
            tasks = route[1:-1]
            if not tasks:
                raise ValueError("empty E2 route cannot be imported as a program")
            previous: str | None = None
            for task in tasks:
                if len(task) < 2 or task[-1] not in {"d", "p"} or task[:-1] not in customers:
                    raise ValueError(f"invalid E2 task {task!r}")
                if task in task_owner:
                    raise ValueError(f"duplicate E2 task {task!r}")
                task_owner[task] = sat_id
                customer = customers[task[:-1]]
                lower[task] = float(customer.tw_del[0] if task.endswith("d") else customer.tw_pick[0])
                if previous is None:
                    arcs.append((
                        f"{sat_id}d",
                        task,
                        float(getattr(problem.nodes[sat_id], "service_time", 0.0))
                        + float(problem.travel_time(sat_id, task[:-1])),
                    ))
                else:
                    arcs.append((
                        previous,
                        task,
                        float(customers[previous[:-1]].service_time)
                        + float(problem.travel_time(previous[:-1], task[:-1])),
                    ))
                previous = task
            arcs.append((
                previous,
                f"{sat_id}p",
                float(customers[previous[:-1]].service_time)
                + float(problem.travel_time(previous[:-1], sat_id)),
            ))

    for customer_id in {task[:-1] for task in task_owner}:
        delivery, pickup = f"{customer_id}d", f"{customer_id}p"
        if delivery not in task_owner or pickup not in task_owner:
            raise ValueError(f"incomplete delivery/pickup pair for {customer_id!r}")
        if task_owner[delivery] != task_owner[pickup]:
            raise ValueError(f"delivery and pickup use different satellites for {customer_id!r}")
        arcs.append((delivery, pickup, float(customers[customer_id].service_time)))

    # Bellman-Ford-style longest-path relaxation gives the earliest schedule
    # for lower-bound arcs.  A change on the |V|-th pass reveals a positive
    # cycle, i.e. incompatible route/predecessor ordering.
    node_count = len(lower)
    for _ in range(node_count):
        changed = False
        for source, target, lag in arcs:
            candidate = lag if source is None else lower[source] + lag
            if candidate > lower[target] + eps:
                lower[target] = candidate
                changed = True
        if not changed:
            break
    else:
        raise ValueError("incompatible M4 route timing creates a positive cycle")

    for task, owner in task_owner.items():
        customer = customers[task[:-1]]
        upper = float(customer.tw_del[1] if task.endswith("d") else customer.tw_pick[1])
        if isfinite(upper) and lower[task] > upper + eps:
            raise ValueError(f"time window violated at {task!r}")

    programs = []
    assignment: dict[str, str] = {}
    for sat_id in used_satellites:
        h = lower[f"{sat_id}d"] + float(getattr(problem.nodes[sat_id], "service_time", 0.0))
        p = lower[f"{sat_id}p"]
        program = make_m4_satellite_program(
            problem,
            sat_id,
            routes_by_used_sat[sat_id],
            ready_time=h,
            pickup_ready_time=p,
            eps=eps,
        )
        programs.append(program)
        for customer_id in program.customers:
            if customer_id in assignment:
                raise ValueError(f"customer {customer_id!r} appears in multiple programs")
            assignment[customer_id] = sat_id

    witness = validate_m4_e1_program_witness(problem, normalized_e1, {program.sat_id: program for program in programs}, eps=eps)
    if not witness.feasible:
        raise ValueError(f"E1 routes do not validate against imported programs: {witness.reason}")
    return M4ImportedProgramSeed(tuple(programs), witness, assignment)
