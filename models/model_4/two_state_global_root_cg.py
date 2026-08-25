"""Audited root column generation over an existing finite M4 state grid.

This module is intentionally narrower than a complete branch-price solver:
it can price every complete satellite program at every *already represented*
``(h, p)`` state.  It does not discover new states or branch, so neither a
closed root LP nor the finite-pool integer master is a global M4 proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

import gurobipy as gp
from gurobipy import GRB

from models.model_4.certified_time_expansion import M4SatelliteProgram
from models.model_4.two_state_global_program_master import build_m4_two_state_global_program_master
from models.model_4.two_state_program_pricing import (
    existing_state_pricing_terms,
    price_m4_satellite_program,
)


@dataclass(frozen=True)
class M4FixedStateRootCGResult:
    """Result whose status makes the deliberately limited proof scope clear."""

    status: str
    proven_optimal: bool
    proof_scope: str
    root_lp_objective: float | None
    columns: tuple[M4SatelliteProgram, ...]
    iterations: int
    pricing_records: tuple[dict, ...]


def run_m4_two_state_fixed_state_root_cg(
    problem,
    initial_programs: Iterable[M4SatelliteProgram],
    vehicles_per_satellite: int,
    *,
    max_iterations: int = 30,
    pricing_time_limit: float = 30.0,
    reduced_cost_tolerance: float = 1e-6,
    fixed_assignment: Mapping[str, str] | None = None,
    forbidden_assignments: Iterable[tuple[str, str]] = (),
) -> M4FixedStateRootCGResult:
    """Close CG only for the supplied finite state grid, never globally.

    A newly priced program has coefficients only in the existing
    ``ProgramAssign`` and ``ProgramStateUse`` rows, so its reduced cost is
    fully accounted for by :func:`existing_state_pricing_terms`.  If pricing
    finds an unseen negative column it is appended and the RMP is rebuilt.
    If it finds a duplicate negative column, the run stops as incomplete: an
    existing variable at its upper bound must not be mistaken for a new
    improving program.
    """

    columns = list(initial_programs)
    if not columns:
        raise ValueError("fixed-state root CG requires at least one seed program")
    fixed = {str(customer_id): str(sat_id) for customer_id, sat_id in (fixed_assignment or {}).items()}
    forbidden = {(str(customer_id), str(sat_id)) for customer_id, sat_id in forbidden_assignments}
    seen = {_program_key(column) for column in columns}
    records: list[dict] = []
    last_obj = None

    for iteration in range(max_iterations):
        model, _, context = build_m4_two_state_global_program_master(
            problem,
            columns,
            vehicles_per_satellite,
            relax=True,
            fixed_assignment=fixed,
            forbidden_assignments=forbidden,
        )
        model.optimize()
        if model.status != GRB.OPTIMAL:
            return _result(
                f"ROOT_MASTER_{_status_name(model.status)}", last_obj, columns, iteration, records
            )
        last_obj = float(model.ObjVal)
        new_columns: list[M4SatelliteProgram] = []
        duplicate_negative = False
        pricing_incomplete = False

        for state in context["states"]:
            terms, state_term = existing_state_pricing_terms(
                model, context, state.sat_id, state.state_idx
            )
            # A column covering a customer at a branch-forbidden satellite
            # can never be positive: ProgramAssign and w=0 would force it
            # out.  Excluding it here is therefore exact and prevents a
            # branch-violating column from re-entering through pricing.
            state_forbidden = {
                customer_id
                for customer_id, blocked_satellite in forbidden
                if blocked_satellite == state.sat_id
            }
            state_forbidden.update(
                customer_id
                for customer_id, fixed_satellite in fixed.items()
                if fixed_satellite != state.sat_id
            )
            priced = price_m4_satellite_program(
                problem,
                state.sat_id,
                delivery_ready_time=state.delivery_ready_time,
                pickup_ready_time=state.pickup_ready_time,
                customer_rc_terms=terms,
                state_rc_term=state_term,
                vehicles_per_satellite=vehicles_per_satellite,
                forbidden_customer_ids=tuple(sorted(state_forbidden)),
                excluded_programs=tuple(
                    column for column in columns
                    if (
                        column.sat_id == state.sat_id
                        and float(column.ready_time) == state.delivery_ready_time
                        and float(column.pickup_ready_time) == state.pickup_ready_time
                    )
                ),
                time_limit=pricing_time_limit,
            )
            record = {
                "iteration": iteration,
                "sat_id": state.sat_id,
                "state_idx": state.state_idx,
                "delivery_ready_time": state.delivery_ready_time,
                "pickup_ready_time": state.pickup_ready_time,
                "status": priced.status,
                "pricing_optimal": priced.pricing_optimal,
                "reduced_cost": priced.reduced_cost,
                "reduced_cost_bound": priced.reduced_cost_bound,
                "selected_customers": list(priced.selected_customers),
                "runtime": priced.runtime,
            }
            records.append(record)
            if not priced.pricing_optimal:
                pricing_incomplete = True
                continue
            if priced.reduced_cost is None or priced.reduced_cost >= -reduced_cost_tolerance:
                continue
            if priced.program is None:
                pricing_incomplete = True
                continue
            if _program_key(priced.program) in seen:
                duplicate_negative = True
                continue
            seen.add(_program_key(priced.program))
            new_columns.append(priced.program)

        if pricing_incomplete:
            return _result("FIXED_STATE_PRICING_INCOMPLETE", last_obj, columns, iteration + 1, records)
        if duplicate_negative:
            return _result("FIXED_STATE_PRICING_DUPLICATE_NEGATIVE", last_obj, columns, iteration + 1, records)
        if not new_columns:
            return _result("FIXED_STATE_ROOT_LP_CLOSED", last_obj, columns, iteration + 1, records)
        columns.extend(new_columns)

    return _result("FIXED_STATE_CG_ITERATION_LIMIT", last_obj, columns, max_iterations, records)


def _program_key(program: M4SatelliteProgram) -> tuple:
    return (
        program.sat_id,
        float(program.ready_time),
        float(program.pickup_ready_time),
        tuple(tuple(route) for route in program.routes),
    )


def _result(status: str, root_lp_objective, columns, iterations: int, records: list[dict]) -> M4FixedStateRootCGResult:
    return M4FixedStateRootCGResult(
        status=status,
        proven_optimal=False,
        proof_scope="restricted_existing_m4_two_state_grid_root_lp_only",
        root_lp_objective=root_lp_objective,
        columns=tuple(columns),
        iterations=iterations,
        pricing_records=tuple(records),
    )


def _status_name(status_code: int) -> str:
    return {
        GRB.OPTIMAL: "OPTIMAL",
        GRB.INFEASIBLE: "INFEASIBLE",
        GRB.INF_OR_UNBD: "INF_OR_UNBD",
        GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.INTERRUPTED: "INTERRUPTED",
        GRB.SUBOPTIMAL: "SUBOPTIMAL",
        GRB.NUMERIC: "NUMERIC",
    }.get(status_code, str(status_code))
