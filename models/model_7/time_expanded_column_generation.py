"""Root column generation for the global M7 time-expanded formulation.

This is intentionally one stage short of a complete branch-price-and-cut
solver.  It can close the *root LP* only: all satellite-ready states are
priced with the exact elementary-route MIP, and it reports a valid global LP
lower bound only when every pricing problem is certified closed and every
phase-I artificial has left the solution.  A fractional root still requires a
branch tree whose rows are passed into pricing, so this module never reports
``proven_optimal=True``.

The point of keeping this driver separate from the restricted route-pool
solvers is contractual: a finite pool integer optimum is an incumbent, while
a priced-complete root LP is a lower bound.  Neither is a 50-customer global
integer proof by itself.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, Mapping, Sequence

from gurobipy import GRB

from models.model_7.certified_time_expansion import ReadyState, enumerate_ready_states
from models.model_7.time_expanded_pricing import price_m7_ready_state
from models.model_7.time_expanded_route_master import (
    audit_time_expanded_reduced_costs,
    build_m7_time_expanded_master,
    extract_time_expanded_pricing_duals,
)


EPS = 1e-6


def singleton_seed_columns(problem) -> list[dict[str, Any]]:
    """Return every physical single-customer E2 path as a safe seed option.

    The ready-state master filters paths that cannot meet a particular state
    time window.  These columns are not required because the root driver has
    phase-I artificials, but supplying them can improve early RMP stability on
    small instances.
    """

    return [
        {
            "sat_id": satellite.id,
            "path": (satellite.id, f"{customer.id}d", satellite.id),
            "source": "singleton_seed",
        }
        for satellite in problem.satellites
        for customer in problem.customers
    ]


def solve_m7_time_expanded_root_cg(
    problem,
    vehicles_per_satellite: int,
    *,
    initial_route_columns: Iterable[Mapping[str, Any]] = (),
    ready_states: Mapping[str, Sequence[ReadyState]] | None = None,
    max_iterations: int = 30,
    pricing_time_limit: float | None = 15.0,
    pricing_pool_solutions: int = 1,
    artificial_cost: float = 1_000_000.0,
    lp_method: int | None = None,
    lp_crossover: int | None = None,
    output_flag: bool = False,
    eps: float = EPS,
) -> Dict[str, Any]:
    """Run a correctness-first M7 global root-LP column-generation loop.

    The formulation retains free assignment and the global E1 arc flow.  A
    route is indexed by a satellite-ready state, so it appears in exactly the
    fixed row families whose duals are sent to ``price_m7_ready_state``.  The
    return value distinguishes three outcomes:

    * ``root_lp_priced_complete``: a valid global LP lower bound was closed;
    * a partial/timeout diagnostic: useful columns/bounds, but no LP proof;
    * a phase-I result with artificials: not even an original-LP bound yet.

    In all cases ``proven_optimal`` remains false because no branch tree is
    explored here.
    """

    if max_iterations <= 0:
        raise ValueError("max_iterations must be positive")
    if pricing_pool_solutions <= 0:
        raise ValueError("pricing_pool_solutions must be positive")
    if artificial_cost <= 0:
        raise ValueError("artificial_cost must be positive")
    if lp_method is not None and not isinstance(lp_method, int):
        raise ValueError("lp_method must be a Gurobi Method integer or None")
    if lp_crossover is not None and not isinstance(lp_crossover, int):
        raise ValueError("lp_crossover must be a Gurobi Crossover integer or None")

    states = (
        {sat_id: tuple(items) for sat_id, items in ready_states.items()}
        if ready_states is not None else enumerate_ready_states(problem)
    )
    raw_columns: list[dict[str, Any]] = []
    seen_unscoped_paths: set[tuple[str, tuple[str, ...]]] = set()
    seen_state_columns: set[tuple[str, tuple[str, ...], int]] = set()
    for raw in initial_route_columns:
        normalised = _normalise_raw_column(raw)
        if normalised is None:
            continue
        if _register_column_key(
            normalised, seen_unscoped_paths, seen_state_columns
        ):
            raw_columns.append(normalised)

    started = time.perf_counter()
    iteration_records: list[dict[str, Any]] = []
    # Phase I obtains a real route cover.  Once its artificial variables have
    # disappeared, rebuild the same RMP without them before interpreting
    # coverage duals for certified pricing; zero-valued artificial variables
    # can otherwise leave a degenerate million-cost dual behind.
    phase_one = True
    final_result: Dict[str, Any] = {
        "model": "M7",
        "method": "time_expanded_root_column_generation",
        "proof_scope": "global_root_lp_only",
        "proven_optimal": False,
        "branch_and_price_complete": False,
        "root_lp_priced_complete": False,
        "route_universe_complete": False,
        "phase_one_complete": False,
        "ready_state_count": sum(len(items) for items in states.values()),
        "initial_raw_columns": len(raw_columns),
        "lp_method": lp_method,
        "lp_crossover": lp_crossover,
        # Persist paths so a later experiment can resume from this audited
        # pool rather than regenerating the same early pricing rounds.
        "raw_route_columns": raw_columns,
        "iterations": iteration_records,
    }

    for iteration in range(1, max_iterations + 1):
        model, variables, context = build_m7_time_expanded_master(
            problem,
            raw_columns,
            vehicles_per_satellite,
            ready_states=states,
            relax=True,
            artificial_cost=artificial_cost if phase_one else None,
        )
        model.setParam("OutputFlag", 1 if output_flag else 0)
        # Different optimal LP duals can generate different route columns in
        # a highly degenerate RMP.  These optional LP-solver settings affect
        # only the priced dual point; the same RC audit and exact closure
        # criterion remain mandatory.
        if lp_method is not None:
            model.setParam("Method", lp_method)
        if lp_crossover is not None:
            model.setParam("Crossover", lp_crossover)
        model.optimize()

        solver_fallback = None
        if model.status != GRB.OPTIMAL and lp_method == 2:
            # A barrier point without crossover can occasionally be reported
            # SUBOPTIMAL on a large, degenerate RMP.  Do not use its duals.
            # Re-solve the *identical* LP with dual simplex; only an OPTIMAL
            # result below is allowed to enter the pricing/certificate path.
            original_status = _status_name(model.status)
            model.reset()
            model.setParam("Method", 1)
            model.setParam("Crossover", -1)
            model.optimize()
            solver_fallback = {
                "from": "barrier",
                "initial_status": original_status,
                "to": "dual_simplex",
                "final_status": _status_name(model.status),
            }

        record: dict[str, Any] = {
            "iteration": iteration,
            "raw_columns_before_pricing": len(raw_columns),
            "expanded_columns": len(context["columns"]),
            "master_status": _status_name(model.status),
            "master_status_code": int(model.status),
            "phase": "phase_one" if phase_one else "original_lp",
            "pricing": [],
        }
        if solver_fallback is not None:
            record["master_solver_fallback"] = solver_fallback
        if model.status != GRB.OPTIMAL:
            record["reason"] = "restricted_master_not_optimal"
            iteration_records.append(record)
            final_result.update({
                "status": "ROOT_MASTER_NOT_OPTIMAL",
                "last_master_status": record["master_status"],
                "elapsed": time.perf_counter() - started,
                "raw_columns": len(raw_columns),
            })
            return final_result

        has_artificial = variables["artificial"] is not None
        artificial_value = (
            sum(variable.X for variable in variables["artificial"].values())
            if has_artificial else 0.0
        )
        record.update({
            "lp_objective_with_artificials": float(model.ObjVal),
            "artificial_value": float(artificial_value),
        })
        try:
            record["reduced_cost_audit_max_error"] = float(
                audit_time_expanded_reduced_costs(model, variables, context)
            )
        except Exception as exc:
            record["reason"] = "reduced_cost_audit_failed"
            record["audit_error"] = repr(exc)
            iteration_records.append(record)
            final_result.update({
                "status": "ROOT_RC_AUDIT_FAILED",
                "elapsed": time.perf_counter() - started,
                "raw_columns": len(raw_columns),
            })
            return final_result

        if phase_one and artificial_value <= eps:
            # Do not price with phase-I duals.  In a degenerate LP, an unused
            # artificial can still determine a cover dual and make an already
            # present route appear spuriously negative.  The next iteration
            # removes those variables and begins genuine root-LP pricing.
            record["phase_transition"] = "rebuild_without_artificials"
            iteration_records.append(record)
            final_result.update({
                "phase_one_complete": True,
                "last_lp_objective_with_artificials": float(model.ObjVal),
                "last_artificial_value": float(artificial_value),
                "last_expanded_columns": len(context["columns"]),
                "last_reduced_cost_audit_max_error": record["reduced_cost_audit_max_error"],
            })
            phase_one = False
            continue

        duals = extract_time_expanded_pricing_duals(model, context)
        known_paths_by_state: dict[tuple[str, int], set[tuple[str, ...]]] = {
            (sat_id, state_idx): set()
            for sat_id in context["F"]
            for state_idx in range(len(context["states"][sat_id]))
        }
        for column in context["columns"]:
            known_paths_by_state[column.sat_id, column.state_idx].add(column.path)
        new_columns: list[dict[str, Any]] = []
        pricing_closed = True
        saw_negative = False
        for sat_id in context["F"]:
            for state_idx, state in enumerate(context["states"][sat_id]):
                priced = price_m7_ready_state(
                    problem,
                    sat_id,
                    state.ready_time,
                    state_idx=state_idx,
                    cover_duals=duals["cover"],
                    vehicle_dual=duals["vehicle"][sat_id],
                    state_use_dual=duals["state_use"][sat_id, state_idx],
                    state_customer_duals={
                        customer_id: duals["state_customer"][
                            customer_id, sat_id, state_idx
                        ]
                        for customer_id in context["C"]
                    },
                    excluded_paths=known_paths_by_state[sat_id, state_idx],
                    time_limit=pricing_time_limit,
                    output_flag=False,
                    pool_solutions=pricing_pool_solutions,
                    eps=eps,
                )
                pricing_record = {
                    "satellite": sat_id,
                    "state_idx": state_idx,
                    "ready_time": state.ready_time,
                    "status": priced.status,
                    "best_reduced_cost": priced.best_reduced_cost,
                    "reduced_cost_lower_bound": priced.reduced_cost_lower_bound,
                    "proven_no_negative_column": priced.proven_no_negative_column,
                    "negative_columns": len(priced.columns),
                    "runtime": priced.runtime,
                }
                record["pricing"].append(pricing_record)
                if priced.proven_no_negative_column:
                    continue
                pricing_closed = False
                if priced.columns:
                    saw_negative = True
                    for raw in priced.columns:
                        normalised = _normalise_raw_column(raw)
                        if normalised is None:
                            continue
                        if _register_column_key(
                            normalised, seen_unscoped_paths, seen_state_columns
                        ):
                            new_columns.append(normalised)

        record["new_raw_columns"] = len(new_columns)
        iteration_records.append(record)
        final_result.update({
            "last_lp_objective_with_artificials": float(model.ObjVal),
            "last_artificial_value": float(artificial_value),
            "last_expanded_columns": len(context["columns"]),
            "last_reduced_cost_audit_max_error": record["reduced_cost_audit_max_error"],
        })

        if pricing_closed:
            if artificial_value <= eps:
                final_result.update({
                    "status": "ROOT_LP_PRICED_COMPLETE",
                    "solution_type": "root_lp_priced_complete",
                    "root_lp_priced_complete": True,
                    "route_universe_complete": True,
                    "root_lp_lower_bound": float(model.ObjVal),
                })
            else:
                final_result.update({
                    "status": "ROOT_LP_CLOSED_WITH_ARTIFICIALS",
                    "solution_type": "root_lp_phase_one_incomplete",
                    "route_universe_complete": False,
                    "reason": "all priced routes closed but phase-I artificials remain",
                })
            final_result.update({
                "elapsed": time.perf_counter() - started,
                "raw_columns": len(raw_columns),
            })
            return final_result

        if not new_columns:
            # A pricing time limit can leave a possible negative column with
            # no incumbent.  Do not loop forever or falsely close the LP.
            final_result.update({
                "status": "ROOT_PRICING_UNRESOLVED",
                "solution_type": "root_lp_not_proven",
                "reason": (
                    "pricing did not close and yielded no new distinct "
                    "negative route column"
                ),
                "elapsed": time.perf_counter() - started,
                "raw_columns": len(raw_columns),
                "saw_negative_pricing_incumbent": saw_negative,
            })
            return final_result

        raw_columns.extend(new_columns)

    final_result.update({
        "status": "ROOT_CG_ITERATION_LIMIT",
        "solution_type": "root_lp_not_proven",
        "reason": "column-generation iteration limit reached",
        "elapsed": time.perf_counter() - started,
        "raw_columns": len(raw_columns),
    })
    return final_result


def _normalise_raw_column(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    """Keep only path information that the state-expansion builder trusts."""

    path = tuple(raw.get("path", ()))
    sat_id = str(raw.get("sat_id") or (path[0] if path else ""))
    if len(path) < 3 or not sat_id or path[0] != sat_id or path[-1] != sat_id:
        return None
    result = {
        "sat_id": sat_id,
        "path": path,
        "source": str(raw.get("source", "root_cg")),
    }
    # Pricing is state-specific.  Keeping this scope is essential: a route
    # priced at (s,h) has the state-use dual of that particular h, and must
    # enter the RMP in that column position rather than being silently
    # replicated across every ready state.
    if raw.get("state_idx") is not None:
        try:
            state_idx = int(raw["state_idx"])
        except (TypeError, ValueError):
            return None
        if state_idx < 0:
            return None
        result["state_idx"] = state_idx
    return result


def _register_column_key(
    column: Mapping[str, Any],
    seen_unscoped_paths: set[tuple[str, tuple[str, ...]]],
    seen_state_columns: set[tuple[str, tuple[str, ...], int]],
) -> bool:
    """Return whether a raw route/state column is genuinely new.

    An unscoped seed expands to all feasible ready states; a later scoped
    pricing result for the same physical path is then already present.  Two
    scoped pricing results at different ready states, however, are distinct
    columns and must both be retained.
    """

    path_key = (str(column["sat_id"]), tuple(column["path"]))
    state_idx = column.get("state_idx")
    if state_idx is None:
        if path_key in seen_unscoped_paths:
            return False
        seen_unscoped_paths.add(path_key)
        return True
    if path_key in seen_unscoped_paths:
        return False
    state_key = (path_key[0], path_key[1], int(state_idx))
    if state_key in seen_state_columns:
        return False
    seen_state_columns.add(state_key)
    return True


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
