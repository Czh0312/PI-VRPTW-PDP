"""Exact Model 7 decomposition: free assignment plus asynchronous relay.

The complete Model 7 feasible region is partitioned by customer--satellite
assignment.  For a fixed assignment, the remaining problem is exactly the
complete compact Model 6 MILP.  Exhaustively solving every mutually exclusive
assignment partition therefore gives a globally exact Model 7 solution.

This representation is intentionally aimed at small-instance certification.
The ALNS implementation is used for larger instances.
"""

from __future__ import annotations

from itertools import product

from models.routing.common.task_builder import build_tasks_from_assignment


def assignment_count(problem) -> int:
    """Return the number of complete customer--satellite partitions."""
    return len(problem.satellites) ** len(problem.customers)


def _assignment_key(problem, assignment):
    return tuple(assignment[customer.id] for customer in problem.customers)


def iter_assignments(problem):
    """Yield every assignment exactly once, with the input assignment first."""
    customers = [customer.id for customer in problem.customers]
    satellites = [satellite.id for satellite in problem.satellites]
    if customers and not satellites:
        return

    seen = set()
    original = {
        customer.id: customer.assigned_sat
        for customer in problem.customers
        if customer.assigned_sat in satellites
    }
    if len(original) == len(customers):
        key = _assignment_key(problem, original)
        seen.add(key)
        yield original

    for values in product(satellites, repeat=len(customers)):
        if values in seen:
            continue
        seen.add(values)
        yield dict(zip(customers, values))


def build_assignment_subproblem(problem, assignment):
    """Build the exact fixed-assignment task catalogue for one partition."""
    expected = {customer.id for customer in problem.customers}
    if set(assignment) != expected:
        missing = sorted(expected - set(assignment))
        extra = sorted(set(assignment) - expected)
        raise ValueError(
            f"Model 7 assignment must cover every customer; "
            f"missing={missing}, extra={extra}"
        )
    satellites = {satellite.id for satellite in problem.satellites}
    invalid = sorted(set(assignment.values()) - satellites)
    if invalid:
        raise ValueError(f"unknown Model 7 satellites: {invalid}")
    return build_tasks_from_assignment(problem, assignment)


def build_model(problem, assignment, vehicles_per_satellite=None,
                TFp=9999.0, strengthen=True):
    """Build one exact compact MILP partition of the complete Model 7."""
    if vehicles_per_satellite is None:
        vehicles_per_satellite = problem.fleet.e2_per_sat_count
    e1_tasks, e2_tasks_by_sat = build_assignment_subproblem(
        problem, assignment)
    from models.routing.variants.model_6.model import build_model as build_m6

    return build_m6(
        problem,
        e1_tasks,
        e2_tasks_by_sat,
        vehicles_per_satellite,
        TFp=TFp,
        strengthen=strengthen,
    )


__all__ = [
    "assignment_count",
    "iter_assignments",
    "build_assignment_subproblem",
    "build_model",
]
