"""Small, correctness-first external branch-price controller for M7.

This controller sits above the global time-expanded M7 master and its exact
fixed-ready-state pricing MIP.  It never delegates the branch tree to a
restricted-pool MIP: before a node receives a bound or is pruned, every
satellite-ready state is priced to a certified no-negative-column result.

Scope deliberately remains modest:

* binary master variables are branched externally by value;
* route variables are branched with Ryan--Foster customer-pair rows, whose
  duals and same/different restrictions are passed to pricing;
* an unusual fractional route solution with no fractional customer pair is
  reported as unsupported rather than being falsely certified.

The last behaviour is intentional.  It makes this a safe 5-customer proof
prototype and a diagnostic foundation for larger instances, not a claim that
every 50-customer instance is already solved by complete B&P.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Dict, Iterable, Mapping, Sequence

from gurobipy import GRB

from models.model_7.certified_time_expansion import ReadyState, enumerate_ready_states
from models.model_7.time_expanded_column_generation import singleton_seed_columns
from models.model_7.time_expanded_pricing import price_m7_ready_state
from models.model_7.time_expanded_route_master import (
    build_m7_time_expanded_master,
    extract_time_expanded_pricing_duals,
)


EPS = 1e-6


@dataclass
class _BranchNode:
    node_id: int
    depth: int
    fixings: Dict[tuple[str, tuple[Any, ...]], int] = field(default_factory=dict)
    relations: Dict[tuple[str, str], str] = field(default_factory=dict)
    # (satellite, state-index subset) -> 0 means select outside the subset;
    # -> 1 means select inside it whenever the satellite is served.
    state_partitions: Dict[tuple[str, tuple[int, ...]], int] = field(default_factory=dict)


def solve_m7_time_expanded_branch_price(
    problem,
    vehicles_per_satellite: int,
    *,
    initial_route_columns: Iterable[Mapping[str, Any]] | None = None,
    ready_states: Mapping[str, Sequence[ReadyState]] | None = None,
    resume_state: Mapping[str, Any] | None = None,
    validated_incumbent_cost: float | None = None,
    max_nodes: int = 64,
    max_cg_iterations_per_node: int = 30,
    pricing_time_limit: float | None = 15.0,
    pricing_pool_solutions: int = 1,
    artificial_cost: float = 1_000_000.0,
    lp_method: int | None = None,
    lp_crossover: int | None = None,
    wall_time_limit: float | None = None,
    node_time_limit: float | None = None,
    output_flag: bool = False,
    eps: float = EPS,
) -> Dict[str, Any]:
    """Run a bounded external M7 branch-price proof attempt.

    ``validated_incumbent_cost`` must be the objective of a separately
    validated feasible M7 solution.  It is an upper bound only; an equality
    with a priced-complete node LP bound may prune that node.  If it is absent,
    the controller can still obtain an incumbent from an integral priced node.

    The method returns ``proven_optimal=True`` *only* after every branch leaf
    was either priced-complete and pruned/integral, and a feasible incumbent
    is available.  Any pricing timeout, phase-I residue, unsupported
    fractional pattern, or node limit leaves that flag false.
    """

    if max_nodes <= 0:
        raise ValueError("max_nodes must be positive")
    if max_cg_iterations_per_node <= 0:
        raise ValueError("max_cg_iterations_per_node must be positive")
    if pricing_pool_solutions <= 0:
        raise ValueError("pricing_pool_solutions must be positive")
    if artificial_cost <= 0:
        raise ValueError("artificial_cost must be positive")
    if lp_method is not None and not isinstance(lp_method, int):
        raise ValueError("lp_method must be a Gurobi Method integer or None")
    if lp_crossover is not None and not isinstance(lp_crossover, int):
        raise ValueError("lp_crossover must be a Gurobi Crossover integer or None")
    if wall_time_limit is not None and wall_time_limit <= 0:
        raise ValueError("wall_time_limit must be positive or None")
    if node_time_limit is not None and node_time_limit <= 0:
        raise ValueError("node_time_limit must be positive or None")
    if validated_incumbent_cost is not None and validated_incumbent_cost < -eps:
        raise ValueError("validated_incumbent_cost must be non-negative")

    states = (
        {sat_id: tuple(items) for sat_id, items in ready_states.items()}
        if ready_states is not None
        else enumerate_ready_states(problem)
    )
    raw_columns, seen_unscoped, seen_state = _seed_columns(
        initial_route_columns if initial_route_columns is not None else singleton_seed_columns(problem)
    )
    incumbent = float(validated_incumbent_cost) if validated_incumbent_cost is not None else None
    incumbent_source = "caller_validated" if incumbent is not None else None
    started = time.perf_counter()
    resume_records = (
        [dict(record) for record in resume_state.get("nodes", ())]
        if resume_state is not None else []
    )
    unresolved: list[dict[str, Any]] = []
    queue = (
        [_deserialise_node(node) for node in resume_state.get("pending_nodes", ())]
        if resume_state is not None else [_BranchNode(node_id=0, depth=0)]
    )
    if resume_state is not None and not queue:
        raise ValueError("branch-price resume state has no pending_nodes")
    # A checkpoint may include a diagnostic record for a node whose pricing
    # was interrupted.  That node remains in ``pending_nodes`` and will be
    # retried, so remove its non-final record from the live closed-node list
    # rather than duplicating the same node ID on every resume.  Preserve the
    # old diagnostic separately for auditability.
    pending_ids = {node.node_id for node in queue}
    resumed_partial_records = [
        record for record in resume_records if record.get("node_id") in pending_ids
    ]
    records: list[dict[str, Any]] = [
        record for record in resume_records if record.get("node_id") not in pending_ids
    ]
    existing_node_ids = [record.get("node_id", -1) for record in records]
    existing_node_ids.extend(node.node_id for node in queue)
    next_node_id = max(existing_node_ids, default=-1) + 1
    processed_this_run = 0
    processed_total = len(records)

    result: Dict[str, Any] = {
        "model": "M7",
        "method": "time_expanded_external_branch_price",
        "proof_scope": "global_branch_price_if_tree_closed",
        "proven_optimal": False,
        "branch_and_price_complete": False,
        "root_lp_priced_complete": bool(
            resume_state and resume_state.get("root_lp_priced_complete", False)
        ),
        "route_universe_complete": False,
        "initial_raw_columns": len(raw_columns),
        "ready_state_count": sum(len(values) for values in states.values()),
        "lp_method": lp_method,
        "lp_crossover": lp_crossover,
        "wall_time_limit": wall_time_limit,
        "node_time_limit": node_time_limit,
        "resumed_from_checkpoint": resume_state is not None,
        "prior_processed_nodes": len(records),
        "resumed_partial_node_records": resumed_partial_records,
        "nodes": records,
        "unresolved_nodes": unresolved,
    }

    while queue:
        elapsed = time.perf_counter() - started
        if wall_time_limit is not None and elapsed >= wall_time_limit:
            unresolved.append({
                "reason": "global_wall_time_limit",
                "queued_nodes": [node.node_id for node in queue],
                "elapsed": elapsed,
            })
            break
        if processed_this_run >= max_nodes:
            unresolved.append({
                "reason": "node_limit",
                "queued_nodes": [node.node_id for node in queue],
            })
            break
        node = queue.pop()
        processed_this_run += 1
        processed_total += 1
        remaining_global = (
            None if wall_time_limit is None
            else max(0.0, wall_time_limit - (time.perf_counter() - started))
        )
        effective_node_limit = node_time_limit
        if remaining_global is not None:
            effective_node_limit = (
                remaining_global if effective_node_limit is None
                else min(effective_node_limit, remaining_global)
            )
        node_deadline = (
            None if effective_node_limit is None
            else time.perf_counter() + effective_node_limit
        )
        node_outcome = _price_node_to_completion(
            problem,
            vehicles_per_satellite,
            states,
            node,
            raw_columns,
            seen_unscoped,
            seen_state,
            max_cg_iterations=max_cg_iterations_per_node,
            pricing_time_limit=pricing_time_limit,
            pricing_pool_solutions=pricing_pool_solutions,
            artificial_cost=artificial_cost,
            lp_method=lp_method,
            lp_crossover=lp_crossover,
            deadline=node_deadline,
            output_flag=output_flag,
            eps=eps,
        )
        record = node_outcome["record"]
        records.append(record)

        if node.node_id == 0:
            result["root_lp_priced_complete"] = bool(node_outcome["complete"])

        if node_outcome.get("infeasible", False):
            # Phase I has artificial variables for every E2 coverage row (and
            # for a Ryan--Foster ``same`` row).  Thus an infeasible Phase-I
            # RMP cannot be repaired by pricing another E2 route: it is an
            # infeasible E1/state/branch combination and is a safe leaf.
            record["fathom"] = "infeasible"
            continue

        if not node_outcome["complete"]:
            unresolved.append({
                "node_id": node.node_id,
                "depth": node.depth,
                "reason": node_outcome["reason"],
            })
            # Preserve the interrupted node itself alongside its siblings so
            # an audit/checkpoint contains the full remaining tree state.
            # It is not treated as closed merely because a wall-clock budget
            # interrupted its pricing loop.
            queue.append(node)
            break

        lower_bound = float(node_outcome["lower_bound"])
        record["node_lp_lower_bound"] = lower_bound
        if node.node_id == 0 and incumbent is not None and lower_bound > incumbent + eps:
            # At the root a validated feasible incumbent must dominate the
            # LP lower bound.  A violation there signals a bad incumbent or a
            # numerical/model inconsistency.  For a *child* node, however,
            # lower_bound > incumbent is the ordinary bound-fathoming case.
            unresolved.append({
                "node_id": node.node_id,
                "depth": node.depth,
                "reason": "root_bound_exceeds_validated_incumbent",
                "node_lower_bound": lower_bound,
                "incumbent": incumbent,
            })
            break
        if incumbent is not None and lower_bound >= incumbent - eps:
            record["fathom"] = "bound"
            continue

        branch_choice = node_outcome["branch_choice"]
        if branch_choice["kind"] == "integral":
            candidate = lower_bound
            if incumbent is None or candidate < incumbent - eps:
                incumbent = candidate
                incumbent_source = f"integral_priced_node_{node.node_id}"
            record["fathom"] = "integral"
            record["incumbent_candidate"] = candidate
            continue
        if branch_choice["kind"] == "unsupported":
            unresolved.append({
                "node_id": node.node_id,
                "depth": node.depth,
                "reason": branch_choice["reason"],
            })
            queue.append(node)
            break

        if branch_choice["kind"] == "variable":
            child_zero = _child_with_fixing(node, next_node_id, branch_choice["family"], branch_choice["key"], 0)
            next_node_id += 1
            child_one = _child_with_fixing(node, next_node_id, branch_choice["family"], branch_choice["key"], 1)
            next_node_id += 1
            queue.extend((child_zero, child_one))
            record["branch"] = {
                "type": "binary_variable",
                "family": branch_choice["family"],
                "key": list(branch_choice["key"]),
            }
            continue

        if branch_choice["kind"] == "ready_state_partition":
            child_outside = _child_with_state_partition(
                node,
                next_node_id,
                branch_choice["satellite"],
                branch_choice["state_indices"],
                0,
            )
            next_node_id += 1
            child_inside = _child_with_state_partition(
                node,
                next_node_id,
                branch_choice["satellite"],
                branch_choice["state_indices"],
                1,
            )
            next_node_id += 1
            queue.extend((child_outside, child_inside))
            record["branch"] = {
                "type": "ready_state_partition",
                "satellite": branch_choice["satellite"],
                "state_indices": list(branch_choice["state_indices"]),
                "selected_mass": branch_choice["selected_mass"],
            }
            continue

        if branch_choice["kind"] == "ryan_foster":
            pair = branch_choice["pair"]
            child_same = _child_with_relation(node, next_node_id, pair, "same")
            next_node_id += 1
            child_different = _child_with_relation(node, next_node_id, pair, "different")
            next_node_id += 1
            queue.extend((child_same, child_different))
            record["branch"] = {
                "type": "ryan_foster",
                "pair": list(pair),
                "together_value": branch_choice["together_value"],
            }
            continue

        unresolved.append({
            "node_id": node.node_id,
            "depth": node.depth,
            "reason": f"unknown_branch_choice:{branch_choice!r}",
        })
        break

    result.update({
        "elapsed": time.perf_counter() - started,
        "processed_nodes": processed_total,
        "processed_nodes_this_run": processed_this_run,
        "raw_columns": len(raw_columns),
        "raw_route_columns": list(raw_columns),
        "pending_nodes": [_serialise_node(node) for node in queue],
        "incumbent": incumbent,
        "incumbent_source": incumbent_source,
    })
    if not queue and not unresolved and incumbent is not None:
        result.update({
            "status": "OPTIMAL",
            "solution_type": "proven_optimal",
            "proven_optimal": True,
            "branch_and_price_complete": True,
            "route_universe_complete": True,
            "objective": incumbent,
            "best_bound": incumbent,
            "mip_gap": 0.0,
        })
    else:
        result.update({
            "status": "INCOMPLETE",
            "solution_type": "branch_price_not_proven",
            "objective": incumbent,
            "best_bound": None,
            "mip_gap": None,
        })
    return result


def _price_node_to_completion(
    problem,
    vehicles_per_satellite: int,
    states: Mapping[str, Sequence[ReadyState]],
    node: _BranchNode,
    raw_columns: list[dict[str, Any]],
    seen_unscoped: set[tuple[str, tuple[str, ...]]],
    seen_state: set[tuple[str, tuple[str, ...], int]],
    *,
    max_cg_iterations: int,
    pricing_time_limit: float | None,
    pricing_pool_solutions: int,
    artificial_cost: float,
    lp_method: int | None,
    lp_crossover: int | None,
    deadline: float | None,
    output_flag: bool,
    eps: float,
) -> dict[str, Any]:
    """Close one LP node, or return an explicit non-certificate reason."""

    phase_one = True
    record: dict[str, Any] = {
        "node_id": node.node_id,
        "depth": node.depth,
        "fixings": _serialise_fixings(node.fixings),
        "relations": {"|".join(pair): relation for pair, relation in node.relations.items()},
        "state_partitions": _serialise_state_partitions(node.state_partitions),
        "cg_iterations": [],
    }

    for iteration in range(1, max_cg_iterations + 1):
        if deadline is not None and time.perf_counter() >= deadline:
            return {"complete": False, "reason": "node_wall_time_limit", "record": record}
        model, variables, context = build_m7_time_expanded_master(
            problem,
            raw_columns,
            vehicles_per_satellite,
            ready_states=states,
            relax=True,
            artificial_cost=artificial_cost if phase_one else None,
        )
        applied = _apply_node_constraints(
            model,
            variables,
            context,
            node,
            phase_one=phase_one,
            artificial_cost=artificial_cost,
        )
        model.setParam("OutputFlag", 1 if output_flag else 0)
        if lp_method is not None:
            model.setParam("Method", lp_method)
        if lp_crossover is not None:
            model.setParam("Crossover", lp_crossover)
        if deadline is not None:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return {"complete": False, "reason": "node_wall_time_limit", "record": record}
            model.setParam("TimeLimit", remaining)
        model.optimize()

        solver_fallback = None
        if model.status != GRB.OPTIMAL and lp_method == 2:
            # Never price against a SUBOPTIMAL barrier dual.  The fallback
            # re-solves the exact same node RMP with dual simplex; failure to
            # reach OPTIMAL remains an explicit incomplete-node outcome.
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
        iteration_record: dict[str, Any] = {
            "iteration": iteration,
            "phase": "phase_one" if phase_one else "original_lp",
            "raw_columns_before_pricing": len(raw_columns),
            "expanded_columns": len(context["columns"]),
            "master_status": _status_name(model.status),
            "pricing": [],
        }
        if solver_fallback is not None:
            iteration_record["master_solver_fallback"] = solver_fallback
        record["cg_iterations"].append(iteration_record)
        if model.status == GRB.INFEASIBLE:
            # Phase I relaxes every row whose feasibility can be restored by
            # a new route column.  If it is still infeasible, the current
            # branch decisions contradict the globally retained E1/state
            # constraints, so this B&B node may be fathomed without pricing.
            iteration_record["fathom"] = "infeasible_phase_one"
            return {"complete": True, "infeasible": True, "record": record}
        if model.status != GRB.OPTIMAL:
            return {"complete": False, "reason": "restricted_node_master_not_optimal", "record": record}

        artificial = variables["artificial"]
        coverage_artificial_value = (
            sum(variable.X for variable in artificial.values()) if artificial is not None else 0.0
        )
        rf_artificial_value = sum(variable.X for variable in applied["rf_artificial"].values())
        artificial_value = coverage_artificial_value + rf_artificial_value
        iteration_record.update({
            "lp_objective": float(model.ObjVal),
            "artificial_value": float(artificial_value),
            "coverage_artificial_value": float(coverage_artificial_value),
            "rf_artificial_value": float(rf_artificial_value),
        })
        try:
            base_duals = extract_time_expanded_pricing_duals(model, context)
            pair_duals = _extract_pair_duals(model, applied["pair_rows"])
            iteration_record["reduced_cost_audit_max_error"] = _audit_node_reduced_costs(
                model, variables, context, base_duals, pair_duals, eps
            )
        except Exception as exc:
            iteration_record["audit_error"] = repr(exc)
            return {"complete": False, "reason": "node_reduced_cost_audit_failed", "record": record}

        if phase_one and artificial_value <= eps:
            # Rebuild without the fake variables before accepting a lower
            # bound or its duals as part of the original formulation.
            phase_one = False
            iteration_record["phase_transition"] = "rebuild_without_artificials"
            continue

        pricing_closed = True
        new_columns: list[dict[str, Any]] = []
        known_paths_by_state: dict[tuple[str, int], set[tuple[str, ...]]] = {
            (sat_id, state_idx): set()
            for sat_id in context["F"]
            for state_idx in range(len(context["states"][sat_id]))
        }
        for column in context["columns"]:
            known_paths_by_state[column.sat_id, column.state_idx].add(column.path)
        for sat_id in context["F"]:
            for state_idx, state in enumerate(context["states"][sat_id]):
                if deadline is not None and time.perf_counter() >= deadline:
                    return {"complete": False, "reason": "node_wall_time_limit", "record": record}
                effective_pricing_limit = pricing_time_limit
                if deadline is not None:
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        return {"complete": False, "reason": "node_wall_time_limit", "record": record}
                    effective_pricing_limit = (
                        remaining if effective_pricing_limit is None
                        else min(effective_pricing_limit, remaining)
                    )
                priced = price_m7_ready_state(
                    problem,
                    sat_id,
                    state.ready_time,
                    state_idx=state_idx,
                    cover_duals=base_duals["cover"],
                    vehicle_dual=base_duals["vehicle"][sat_id],
                    state_use_dual=base_duals["state_use"][sat_id, state_idx],
                    state_customer_duals={
                        customer_id: base_duals["state_customer"][
                            customer_id, sat_id, state_idx
                        ]
                        for customer_id in context["C"]
                    },
                    excluded_paths=known_paths_by_state[sat_id, state_idx],
                    same_customer_pairs=(pair for pair, relation in node.relations.items() if relation == "same"),
                    different_customer_pairs=(pair for pair, relation in node.relations.items() if relation == "different"),
                    pair_branch_duals=pair_duals,
                    time_limit=effective_pricing_limit,
                    output_flag=False,
                    pool_solutions=pricing_pool_solutions,
                    eps=eps,
                )
                iteration_record["pricing"].append({
                    "satellite": sat_id,
                    "state_idx": state_idx,
                    "ready_time": state.ready_time,
                    "status": priced.status,
                    "best_reduced_cost": priced.best_reduced_cost,
                    "reduced_cost_lower_bound": priced.reduced_cost_lower_bound,
                    "proven_no_negative_column": priced.proven_no_negative_column,
                    "negative_columns": len(priced.columns),
                    "runtime": priced.runtime,
                })
                if priced.proven_no_negative_column:
                    continue
                pricing_closed = False
                for raw in priced.columns:
                    normalised = _normalise_raw_column(raw)
                    if normalised is not None and _register_column_key(normalised, seen_unscoped, seen_state):
                        new_columns.append(normalised)

        iteration_record["new_raw_columns"] = len(new_columns)
        if pricing_closed:
            if artificial_value > eps:
                # Every column family has now been priced to a certified
                # no-negative-column result for the Phase-I objective.  A
                # positive optimum of the artificial relaxation therefore
                # proves that even the complete route universe cannot satisfy
                # this branch's coverage/Ryan--Foster requirements.  This is
                # a globally infeasible node, not an unresolved restricted
                # master.
                iteration_record["fathom"] = "infeasible_positive_phase_one"
                return {"complete": True, "infeasible": True, "record": record}
            branch_choice = _choose_branch(variables, context, node, eps)
            return {
                "complete": True,
                "lower_bound": float(model.ObjVal),
                "branch_choice": branch_choice,
                "record": record,
            }
        if not new_columns:
            return {"complete": False, "reason": "pricing_unresolved_or_duplicate_negative_column", "record": record}
        raw_columns.extend(new_columns)

    return {"complete": False, "reason": "node_cg_iteration_limit", "record": record}


def _apply_node_constraints(
    model,
    variables,
    context,
    node: _BranchNode,
    *,
    phase_one: bool,
    artificial_cost: float,
) -> dict[str, Any]:
    """Add all branch decisions before solving a node RMP.

    Binary-variable fixes contain no route-column coefficient.  Ryan--Foster
    rows are the only extra lambda rows, and their names are returned so the
    controller can include their duals in pricing exactly.
    """

    for index, ((family, key), value) in enumerate(sorted(node.fixings.items(), key=str)):
        if family not in variables:
            raise ValueError(f"unknown branch variable family {family!r}")
        model.addConstr(
            _lookup_branch_variable(variables[family], key) == value,
            f"BP_Fix_{node.node_id}_{index}",
        )
    for index, ((sat_id, state_indices), value) in enumerate(
        sorted(node.state_partitions.items(), key=str)
    ):
        if sat_id not in context["F"] or not state_indices:
            raise ValueError(f"invalid ready-state partition {(sat_id, state_indices)!r}")
        max_state_idx = len(context["states"][sat_id])
        if any(state_idx < 0 or state_idx >= max_state_idx for state_idx in state_indices):
            raise ValueError(f"ready-state partition index out of range: {(sat_id, state_indices)!r}")
        expression = sum(
            variables["ready_state"][sat_id, state_idx]
            for state_idx in state_indices
        )
        rhs = 0.0 if value == 0 else variables["served_s"][sat_id]
        model.addConstr(
            expression == rhs,
            f"BP_StatePartition_{node.node_id}_{index}",
        )
    pair_rows: dict[tuple[str, str], str] = {}
    rf_artificial = {}
    for index, (pair, relation) in enumerate(sorted(node.relations.items())):
        if relation not in {"same", "different"}:
            raise ValueError(f"unknown Ryan-Foster relation {relation!r}")
        route_ids = [
            route_id for route_id, column in enumerate(context["columns"])
            if _covers_both(column, pair)
        ]
        row_name = f"BP_RF_{node.node_id}_{index}"
        expression = sum(variables["lambda"][route_id] for route_id in route_ids)
        if phase_one and relation == "same":
            # A same-branch child may initially have no already-generated
            # column containing the pair.  This artificial prevents a false
            # restricted-RMP infeasibility; pricing sees the row dual and can
            # generate a genuine together route.  It must be zero before any
            # original-LP node bound is accepted.
            artificial = model.addVar(
                lb=0.0,
                ub=1.0,
                obj=float(artificial_cost),
                name=f"BP_RF_Art_{node.node_id}_{index}",
            )
            rf_artificial[pair] = artificial
            expression += artificial
        model.addConstr(expression == (1 if relation == "same" else 0), row_name)
        pair_rows[pair] = row_name
    return {"pair_rows": pair_rows, "rf_artificial": rf_artificial}


def _extract_pair_duals(model, pair_rows: Mapping[tuple[str, str], str]) -> dict[tuple[str, str], float]:
    return {
        pair: float(model.getConstrByName(row_name).Pi)
        for pair, row_name in pair_rows.items()
    }


def _lookup_branch_variable(container, key: tuple[Any, ...]):
    """Look up a tupledict entry, including one-dimensional variable keys."""

    try:
        return container[key]
    except KeyError:
        if len(key) == 1:
            return container[key[0]]
        raise


def _audit_node_reduced_costs(model, variables, context, base_duals, pair_duals, tolerance: float) -> float:
    """Verify every existing route's RC includes every active RF row."""

    max_error = 0.0
    for route_id, column in enumerate(context["columns"]):
        expected = (
            column.cost
            - sum(base_duals["cover"][task_id[:-1], column.sat_id] for task_id in column.tasks)
            - base_duals["vehicle"][column.sat_id]
            - base_duals["state_use"][column.sat_id, column.state_idx]
            - sum(
                base_duals["state_customer"][
                    task_id[:-1], column.sat_id, column.state_idx
                ]
                for task_id in column.tasks
            )
            - sum(dual for pair, dual in pair_duals.items() if _covers_both(column, pair))
        )
        actual = float(variables["lambda"][route_id].RC)
        max_error = max(max_error, abs(actual - expected))
    if max_error > tolerance:
        raise RuntimeError(f"node pricing/master RC mismatch: {max_error:.3e}")
    return max_error


def _choose_branch(variables, context, node: _BranchNode, eps: float) -> dict[str, Any]:
    """Choose a pricing-compatible branch, or safely decline to certify."""

    # Once all discrete E1/assignment/state variables are integral, their
    # McCormick auxiliaries are integral as well.  Prefer a coarse satellite
    # activation or E1-routing decision to an individual customer assignment:
    # it separates many assignments at once.  Within a family, use the value
    # closest to 1/2 instead of iteration order.  The latter used to send the
    # tree repeatedly down an arbitrary customer branch even when a much more
    # balanced, structural split was available.
    for family in ("served_s", "x", "w", "y_lin"):
        candidates = []
        for key, variable in variables[family].items():
            value = float(variable.X)
            if eps < value < 1.0 - eps:
                canonical_key = tuple(key) if isinstance(key, tuple) else (key,)
                candidates.append((abs(value - 0.5), tuple(map(str, canonical_key)), canonical_key))
        if candidates:
            _, _, key = min(candidates)
            return {"kind": "variable", "family": family, "key": key}

    state_partition = _choose_ready_state_partition(variables, context, node, eps)
    if state_partition is not None:
        return state_partition

    # Fallback only if no non-trivial state-set partition exists.  It is
    # mathematically valid but generally weaker than the balanced disjunction
    # above, so it should not be the first choice on 50-customer instances.
    for key, variable in variables["ready_state"].items():
        value = float(variable.X)
        if eps < value < 1.0 - eps:
            return {"kind": "variable", "family": "ready_state", "key": tuple(key)}

    fractional_lambdas = [
        (route_id, float(variable.X))
        for route_id, variable in variables["lambda"].items()
        if eps < float(variable.X) < 1.0 - eps
    ]
    if not fractional_lambdas:
        return {"kind": "integral"}

    best_pair: tuple[str, str] | None = None
    best_value = None
    best_distance = float("inf")
    for pair in combinations(context["C"], 2):
        canonical = _canonical_pair(*pair)
        if canonical in node.relations:
            continue
        # Integral lambda variables may contribute to q as well.  Include all
        # columns so the branch is exactly the Ryan--Foster co-occurrence.
        together = sum(
            float(variable.X)
            for route_id, variable in variables["lambda"].items()
            if _covers_both(context["columns"][route_id], canonical)
        )
        if eps < together < 1.0 - eps:
            distance = abs(together - 0.5)
            if distance < best_distance:
                best_pair, best_value, best_distance = canonical, together, distance
    if best_pair is not None:
        return {"kind": "ryan_foster", "pair": best_pair, "together_value": best_value}

    return {
        "kind": "unsupported",
        "reason": "fractional_route_columns_without_fractional_ryan_foster_pair",
    }


def _choose_ready_state_partition(variables, context, node: _BranchNode, eps: float):
    """Return a balanced, pricing-neutral ready-state disjunction if useful.

    For an integral solution, exactly one state is selected when a satellite
    is served.  Given a subset ``H`` of a satellite's states, the two child
    constraints ``sum(H) g = 0`` and ``sum(H) g = served_s`` are therefore a
    complete, disjoint partition of integral solutions.  They contain no
    route-column coefficient, so no additional pricing dual is needed.
    """

    for sat_id in context["F"]:
        served = float(variables["served_s"][sat_id].X)
        if served <= eps:
            continue
        values = [
            (state_idx, float(variables["ready_state"][sat_id, state_idx].X))
            for state_idx in range(len(context["states"][sat_id]))
        ]
        active = [(state_idx, value) for state_idx, value in values if value > eps]
        if len(active) < 2 or not any(eps < value < 1.0 - eps for _, value in active):
            continue

        # Greedily accumulate high-mass states toward half of the active
        # satellite mass.  This gives a short, balanced branch without an
        # exponential subset search and is deterministic for checkpoints.
        target = served / 2.0
        selected: list[int] = []
        selected_mass = 0.0
        for state_idx, value in sorted(active, key=lambda item: (-item[1], item[0])):
            if selected_mass < target - eps:
                selected.append(state_idx)
                selected_mass += value
        if not selected:
            selected = [active[0][0]]
            selected_mass = active[0][1]
        if len(selected) == len(active):
            state_idx, value = min(
                ((idx, val) for idx, val in active if idx in selected),
                key=lambda item: (item[1], item[0]),
            )
            selected.remove(state_idx)
            selected_mass -= value
        state_indices = tuple(sorted(selected))
        if not state_indices or selected_mass <= eps or selected_mass >= served - eps:
            continue
        key = (sat_id, state_indices)
        if key in node.state_partitions:
            continue
        return {
            "kind": "ready_state_partition",
            "satellite": sat_id,
            "state_indices": state_indices,
            "selected_mass": selected_mass,
        }
    return None


def _child_with_fixing(node: _BranchNode, node_id: int, family: str, key: tuple[Any, ...], value: int) -> _BranchNode:
    fixings = dict(node.fixings)
    fixings[family, tuple(key)] = int(value)
    return _BranchNode(
        node_id=node_id,
        depth=node.depth + 1,
        fixings=fixings,
        relations=dict(node.relations),
        state_partitions=dict(node.state_partitions),
    )


def _child_with_state_partition(
    node: _BranchNode,
    node_id: int,
    sat_id: str,
    state_indices: Sequence[int],
    value: int,
) -> _BranchNode:
    if value not in {0, 1}:
        raise ValueError("ready-state partition branch must be binary")
    partitions = dict(node.state_partitions)
    partitions[sat_id, tuple(sorted(state_indices))] = value
    return _BranchNode(
        node_id=node_id,
        depth=node.depth + 1,
        fixings=dict(node.fixings),
        relations=dict(node.relations),
        state_partitions=partitions,
    )


def _child_with_relation(node: _BranchNode, node_id: int, pair: tuple[str, str], relation: str) -> _BranchNode:
    relations = dict(node.relations)
    relations[pair] = relation
    return _BranchNode(
        node_id=node_id,
        depth=node.depth + 1,
        fixings=dict(node.fixings),
        relations=relations,
        state_partitions=dict(node.state_partitions),
    )


def _covers_both(column, pair: tuple[str, str]) -> bool:
    customers = {task_id[:-1] for task_id in column.tasks}
    return pair[0] in customers and pair[1] in customers


def _seed_columns(initial: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], set, set]:
    raw_columns: list[dict[str, Any]] = []
    seen_unscoped: set[tuple[str, tuple[str, ...]]] = set()
    seen_state: set[tuple[str, tuple[str, ...], int]] = set()
    for raw in initial:
        normalised = _normalise_raw_column(raw)
        if normalised is not None and _register_column_key(normalised, seen_unscoped, seen_state):
            raw_columns.append(normalised)
    return raw_columns, seen_unscoped, seen_state


def _normalise_raw_column(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    path = tuple(raw.get("path", ()))
    sat_id = str(raw.get("sat_id") or (path[0] if path else ""))
    if len(path) < 3 or not sat_id or path[0] != sat_id or path[-1] != sat_id:
        return None
    result = {"sat_id": sat_id, "path": path, "source": str(raw.get("source", "branch_price"))}
    if raw.get("state_idx") is not None:
        try:
            state_idx = int(raw["state_idx"])
        except (TypeError, ValueError):
            return None
        if state_idx < 0:
            return None
        result["state_idx"] = state_idx
    return result


def _register_column_key(column, seen_unscoped, seen_state) -> bool:
    path_key = (str(column["sat_id"]), tuple(column["path"]))
    state_idx = column.get("state_idx")
    if state_idx is None:
        if path_key in seen_unscoped:
            return False
        seen_unscoped.add(path_key)
        return True
    if path_key in seen_unscoped:
        return False
    state_key = (path_key[0], path_key[1], int(state_idx))
    if state_key in seen_state:
        return False
    seen_state.add(state_key)
    return True


def _canonical_pair(first: str, second: str) -> tuple[str, str]:
    return (first, second) if first < second else (second, first)


def _serialise_fixings(fixings):
    return [
        {"family": family, "key": list(key), "value": value}
        for (family, key), value in sorted(fixings.items(), key=str)
    ]


def _serialise_state_partitions(partitions):
    return [
        {
            "satellite": sat_id,
            "state_indices": list(state_indices),
            "value": value,
        }
        for (sat_id, state_indices), value in sorted(partitions.items(), key=str)
    ]


def _serialise_node(node: _BranchNode) -> dict[str, Any]:
    """Return the complete branch state needed for an auditable retry."""

    return {
        "node_id": node.node_id,
        "depth": node.depth,
        "fixings": _serialise_fixings(node.fixings),
        "relations": {"|".join(pair): relation for pair, relation in node.relations.items()},
        "state_partitions": _serialise_state_partitions(node.state_partitions),
    }


def _deserialise_node(raw: Mapping[str, Any]) -> _BranchNode:
    """Reconstruct a pending node from this solver's checkpoint schema."""

    try:
        node_id = int(raw["node_id"])
        depth = int(raw["depth"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid branch node checkpoint: {raw!r}") from exc
    if node_id < 0 or depth < 0:
        raise ValueError(f"branch checkpoint has a negative node id/depth: {raw!r}")
    fixings: Dict[tuple[str, tuple[Any, ...]], int] = {}
    for item in raw.get("fixings", ()):
        try:
            family = str(item["family"])
            key = tuple(item["key"])
            value = int(item["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid branch fixing checkpoint: {item!r}") from exc
        if value not in {0, 1}:
            raise ValueError(f"branch fixing is not binary: {item!r}")
        fixings[family, key] = value
    relations: Dict[tuple[str, str], str] = {}
    for raw_pair, relation in raw.get("relations", {}).items():
        pair = tuple(str(part) for part in str(raw_pair).split("|"))
        if len(pair) != 2 or pair[0] == pair[1] or relation not in {"same", "different"}:
            raise ValueError(f"invalid Ryan-Foster checkpoint relation: {(raw_pair, relation)!r}")
        relations[_canonical_pair(*pair)] = str(relation)
    state_partitions: Dict[tuple[str, tuple[int, ...]], int] = {}
    for item in raw.get("state_partitions", ()):
        try:
            sat_id = str(item["satellite"])
            state_indices = tuple(sorted(int(value) for value in item["state_indices"]))
            value = int(item["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid ready-state partition checkpoint: {item!r}") from exc
        if not sat_id or not state_indices or value not in {0, 1}:
            raise ValueError(f"invalid ready-state partition checkpoint: {item!r}")
        state_partitions[sat_id, state_indices] = value
    return _BranchNode(
        node_id=node_id,
        depth=depth,
        fixings=fixings,
        relations=relations,
        state_partitions=state_partitions,
    )


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
