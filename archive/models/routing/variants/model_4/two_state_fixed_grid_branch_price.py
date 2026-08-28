"""Safe branch-price over a *fixed* finite M4 ``(h, p)`` state grid.

This closes the missing integer layer above fixed-state root column
generation.  It branches on customer assignment, selected state, and E1 arcs
while each node prices all complete E2 satellite programs at every state that
is still allowed at that node.

It intentionally does not generate new ``(h, p)`` states.  Therefore its
``fixed_grid_proven_optimal`` flag is never a global M4 certificate; callers
must inspect ``proven_optimal`` (always ``False``) before reporting results.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable, Mapping

import gurobipy as gp
from gurobipy import GRB

from models.routing.variants.model_4.certified_time_expansion import M4SatelliteProgram
from models.routing.variants.model_4.two_state_global_program_master import build_m4_two_state_global_program_master
from models.routing.variants.model_4.two_state_program_pricing import (
    existing_state_pricing_terms,
    price_m4_satellite_program,
)


EPS = 1e-6


@dataclass(frozen=True)
class M4FixedGridBranchNode:
    """Branch restrictions that can be represented in the restricted master."""

    fixed_assignment: tuple[tuple[str, str], ...] = ()
    forbidden_assignments: tuple[tuple[str, str], ...] = ()
    fixed_state_use: tuple[tuple[str, int, int], ...] = ()
    fixed_e1_arcs: tuple[tuple[str, str, int, int], ...] = ()
    fixed_e2_arcs: tuple[tuple[str, int, str, str, int], ...] = ()
    depth: int = 0

    def assignment_map(self) -> dict[str, str]:
        return dict(self.fixed_assignment)

    def state_map(self) -> dict[tuple[str, int], int]:
        return {(sat_id, state_idx): value for sat_id, state_idx, value in self.fixed_state_use}

    def arc_map(self) -> dict[tuple[str, str, int], int]:
        return {(i, j, k): value for i, j, k, value in self.fixed_e1_arcs}

    def e2_arc_map(self) -> dict[tuple[str, int, str, str], int]:
        return {
            (sat_id, state_idx, i, j): value
            for sat_id, state_idx, i, j, value in self.fixed_e2_arcs
        }


@dataclass(frozen=True)
class M4FixedGridBranchPriceResult:
    """Audited result for the finite-state-grid problem only."""

    status: str
    proven_optimal: bool
    fixed_grid_proven_optimal: bool
    proof_scope: str
    objective: float | None
    nodes_processed: int
    columns: tuple[M4SatelliteProgram, ...]
    node_records: tuple[dict, ...]


def solve_m4_two_state_fixed_grid_branch_price(
    problem,
    initial_programs: Iterable[M4SatelliteProgram],
    vehicles_per_satellite: int,
    *,
    max_nodes: int = 128,
    max_cg_iterations_per_node: int = 30,
    pricing_time_limit: float = 30.0,
    pricing_gurobi_params: Mapping[str, object] | None = None,
    pricing_solution_pool_size: int = 1,
    pricing_mode: str = "minimize",
    pricing_subtour_strengthening_size: int = 0,
    exclude_existing_pricing_programs: bool = False,
    max_duplicate_reprices: int = 8,
    reduced_cost_tolerance: float = EPS,
) -> M4FixedGridBranchPriceResult:
    """Solve exactly relative to the seeded finite state grid when possible.

    The initial columns establish the state grid.  Pricing may add any
    previously omitted complete program at those same states.  The routine
    refuses to certify if a state pricer times out, a duplicate negative
    column is encountered, a branch cap is reached, or an unsupported
    fractional program pattern remains.
    """

    columns = list(initial_programs)
    if not columns:
        raise ValueError("fixed-grid branch-price requires at least one seed program")
    seen = {_program_key(column) for column in columns}
    queue = deque([M4FixedGridBranchNode()])
    records: list[dict] = []
    incumbent: float | None = None
    nodes_processed = 0

    while queue:
        if nodes_processed >= max_nodes:
            return _result(
                "FIXED_GRID_NODE_LIMIT", incumbent, False, nodes_processed, columns, records
            )
        node = queue.popleft()
        nodes_processed += 1
        closed = _close_node_lp(
            problem,
            columns,
            seen,
            node,
            vehicles_per_satellite,
            max_cg_iterations=max_cg_iterations_per_node,
            pricing_time_limit=pricing_time_limit,
            pricing_gurobi_params=pricing_gurobi_params,
            pricing_solution_pool_size=pricing_solution_pool_size,
            pricing_mode=pricing_mode,
            pricing_subtour_strengthening_size=pricing_subtour_strengthening_size,
            exclude_existing_pricing_programs=exclude_existing_pricing_programs,
            max_duplicate_reprices=max_duplicate_reprices,
            reduced_cost_tolerance=reduced_cost_tolerance,
        )
        record = {
            "node": nodes_processed,
            "depth": node.depth,
            "cg_status": closed["status"],
            "lp_objective": closed["objective"],
            "columns": len(columns),
            "pricing_records": closed["pricing_records"],
        }
        records.append(record)
        if closed["status"] == "INFEASIBLE":
            continue
        if closed["status"] != "CLOSED":
            return _result(
                f"FIXED_GRID_{closed['status']}", incumbent, False, nodes_processed, columns, records
            )

        model = closed["model"]
        variables = closed["variables"]
        context = closed["context"]
        bound = float(model.ObjVal)
        if incumbent is not None and bound >= incumbent - EPS:
            record["pruned_by_bound"] = True
            continue

        branch = _select_branch(variables, context)
        if branch is None:
            incumbent = bound if incumbent is None else min(incumbent, bound)
            record["integer_leaf"] = True
            continue
        kind, key, value = branch
        record["branch_kind"] = kind
        record["branch_key"] = list(key) if isinstance(key, tuple) else key
        record["branch_value"] = value
        children = _branch_children(node, kind, key)
        if children is None:
            # This refuses to turn an unhandled fractional lambda pattern into
            # a false proof.  In a complete implementation it is where an
            # E2-arc/route branching rule would be added to the pricer.
            return _result(
                "FIXED_GRID_UNSUPPORTED_FRACTIONAL_PROGRAM", incumbent, False,
                nodes_processed, columns, records,
            )
        queue.extend(children)

    return _result("FIXED_GRID_OPTIMAL", incumbent, True, nodes_processed, columns, records)


def _close_node_lp(
    problem,
    columns: list[M4SatelliteProgram],
    seen: set[tuple],
    node: M4FixedGridBranchNode,
    vehicles_per_satellite: int,
    *,
    max_cg_iterations: int,
    pricing_time_limit: float,
    pricing_gurobi_params: Mapping[str, object] | None,
    pricing_solution_pool_size: int,
    pricing_mode: str,
    pricing_subtour_strengthening_size: int,
    exclude_existing_pricing_programs: bool,
    max_duplicate_reprices: int,
    reduced_cost_tolerance: float,
):
    """Close one node LP for all pre-existing states, or return safely early."""

    fixed = node.assignment_map()
    forbidden = set(node.forbidden_assignments)
    fixed_states = node.state_map()
    fixed_arcs = node.arc_map()
    fixed_e2 = node.e2_arc_map()
    pricing_records: list[dict] = []

    if pricing_mode not in {"minimize", "negative_existence"}:
        raise ValueError("pricing_mode must be 'minimize' or 'negative_existence'")

    for iteration in range(max_cg_iterations):
        model, variables, context = build_m4_two_state_global_program_master(
            problem,
            columns,
            vehicles_per_satellite,
            relax=True,
            fixed_assignment=fixed,
            forbidden_assignments=forbidden,
            fixed_state_use=fixed_states,
            fixed_e1_arcs=fixed_arcs,
            fixed_e2_arcs=fixed_e2,
        )
        model.optimize()
        if model.status == GRB.INFEASIBLE:
            return {"status": "INFEASIBLE", "objective": None, "pricing_records": pricing_records}
        if model.status != GRB.OPTIMAL:
            return {"status": f"MASTER_{_status_name(model.status)}", "objective": None, "pricing_records": pricing_records}

        additions: list[M4SatelliteProgram] = []
        duplicate_negative = False
        incomplete = False
        for state in context["states"]:
            state_key = (state.sat_id, state.state_idx)
            if fixed_states.get(state_key) == 0:
                continue
            terms, state_term = existing_state_pricing_terms(model, context, *state_key)
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
            required_e2_arcs = tuple(
                (i, j)
                for (sat_id, state_idx, i, j), value in fixed_e2.items()
                if sat_id == state.sat_id and state_idx == state.state_idx and value == 1
            )
            forbidden_e2_arcs = tuple(
                (i, j)
                for (sat_id, state_idx, i, j), value in fixed_e2.items()
                if sat_id == state.sat_id and state_idx == state.state_idx and value == 0
            )
            state_columns = tuple(
                column for column in columns
                if (
                    column.sat_id == state.sat_id
                    and float(column.ready_time) == state.delivery_ready_time
                    and float(column.pickup_ready_time) == state.pickup_ready_time
                )
            )
            # A full set of labelled no-goods is correct but becomes costly
            # late in CG.  Start without it: at a solved RMP, an unrestricted
            # existing column should have nonnegative RC.  If numerical
            # degeneracy does return a duplicate negative program, exclude
            # just that witness and reprice.  This retains correctness while
            # avoiding O(pool-size) binary conjunctions in the common case.
            retry_exclusions = list(state_columns) if exclude_existing_pricing_programs else []
            repriced = False
            for reprice_attempt in range(max(0, int(max_duplicate_reprices)) + 1):
                priced = price_m4_satellite_program(
                    problem,
                    state.sat_id,
                    delivery_ready_time=state.delivery_ready_time,
                    pickup_ready_time=state.pickup_ready_time,
                    customer_rc_terms=terms,
                    state_rc_term=state_term,
                    vehicles_per_satellite=vehicles_per_satellite,
                    forbidden_customer_ids=tuple(sorted(state_forbidden)),
                    required_arcs=required_e2_arcs,
                    forbidden_arcs=forbidden_e2_arcs,
                    excluded_programs=tuple(retry_exclusions),
                    time_limit=pricing_time_limit,
                    gurobi_params=pricing_gurobi_params,
                    solution_pool_size=pricing_solution_pool_size,
                    prove_no_negative_only=pricing_mode == "negative_existence",
                    reduced_cost_tolerance=reduced_cost_tolerance,
                    subtour_strengthening_size=pricing_subtour_strengthening_size,
                )
                pricing_records.append({
                    "iteration": iteration,
                    "reprice_attempt": reprice_attempt,
                    "sat_id": state.sat_id,
                    "state_idx": state.state_idx,
                    "status": priced.status,
                    "pricing_optimal": priced.pricing_optimal,
                    "pricing_closed": priced.pricing_closed,
                    "reduced_cost": priced.reduced_cost,
                    "reduced_cost_bound": priced.reduced_cost_bound,
                    "selected_customers": list(priced.selected_customers),
                })
                if priced.pricing_closed:
                    break
                new_candidates = []
                duplicate_candidates = []
                # An incumbent returned on a pricing time limit is still an
                # independently validated feasible program with an actually
                # negative reduced cost.  Keeping it can only strengthen the
                # next restricted master; it must not certify this node.
                for candidate in priced.programs:
                    key = _program_key(candidate)
                    if key in seen:
                        duplicate_candidates.append(candidate)
                    else:
                        seen.add(key)
                        additions.append(candidate)
                        new_candidates.append(candidate)
                if new_candidates:
                    break
                if not priced.pricing_optimal:
                    incomplete = True
                    break
                if priced.reduced_cost is None or priced.reduced_cost >= -reduced_cost_tolerance:
                    break
                if not duplicate_candidates:
                    incomplete = True
                    break
                if exclude_existing_pricing_programs:
                    duplicate_negative = True
                    break
                new_retry_keys = {_program_key(column) for column in retry_exclusions}
                retryable = [
                    candidate for candidate in duplicate_candidates
                    if _program_key(candidate) not in new_retry_keys
                ]
                if not retryable or reprice_attempt >= int(max_duplicate_reprices):
                    duplicate_negative = True
                    break
                retry_exclusions.extend(retryable)
                repriced = True
            if repriced:
                # Records carry every reprice; no extra status is needed.
                pass

        if incomplete:
            # Persisting valid columns before returning is important for a
            # resumable run.  The caller still receives PRICING_INCOMPLETE,
            # never a fixed-grid or global certificate.
            columns.extend(additions)
            return {"status": "PRICING_INCOMPLETE", "objective": float(model.ObjVal), "pricing_records": pricing_records}
        # A pool may contain both a duplicate (for example, two MIP integer
        # solutions with the same labelled routes) and new columns.  Keep the
        # new information; fail only when the pricing optimum is represented
        # exclusively by an already-present column.
        if duplicate_negative and not additions:
            return {"status": "DUPLICATE_NEGATIVE", "objective": float(model.ObjVal), "pricing_records": pricing_records}
        if not additions:
            return {
                "status": "CLOSED",
                "objective": float(model.ObjVal),
                "pricing_records": pricing_records,
                "model": model,
                "variables": variables,
                "context": context,
            }
        columns.extend(additions)

    return {"status": "CG_ITERATION_LIMIT", "objective": None, "pricing_records": pricing_records}


def _select_branch(variables, context):
    """Choose a supported fractional master decision deterministically."""

    for kind in ("w", "state_used", "x"):
        for key, variable in variables[kind].items():
            if EPS < variable.X < 1.0 - EPS:
                return kind, key, float(variable.X)
    # Once the state has become integral, branch on a fractional aggregate
    # E2 arc.  Its two children can be enforced in both the RMP and pricing.
    columns = context["columns"]
    column_state = context["column_state"]
    for state in context["states"]:
        if variables["state_used"][state.sat_id, state.state_idx].X < 1.0 - EPS:
            continue
        arc_values: dict[tuple[str, str], float] = {}
        for rid, column in enumerate(columns):
            if column.sat_id != state.sat_id or column_state[rid] != state.state_idx:
                continue
            coefficient = variables["program"][rid].X
            if coefficient <= EPS:
                continue
            for route in column.routes:
                for i, j in zip(route, route[1:]):
                    arc_values[i, j] = arc_values.get((i, j), 0.0) + coefficient
        for (i, j), value in sorted(arc_values.items()):
            if EPS < value < 1.0 - EPS:
                return "e2_arc", (state.sat_id, state.state_idx, i, j), value
    for _, variable in variables["program"].items():
        if EPS < variable.X < 1.0 - EPS:
            return "program", None, float(variable.X)
    return None


def _branch_children(node: M4FixedGridBranchNode, kind: str, key):
    """Return two exact children for each currently supported variable."""

    fixed = node.assignment_map()
    forbidden = set(node.forbidden_assignments)
    states = node.state_map()
    arcs = node.arc_map()
    e2_arcs = node.e2_arc_map()
    if kind == "w":
        customer_id, sat_id = key
        if customer_id in fixed:
            return None
        yes_fixed = dict(fixed)
        yes_fixed[customer_id] = sat_id
        no_forbidden = set(forbidden)
        no_forbidden.add((customer_id, sat_id))
        return (
            _node(yes_fixed, forbidden, states, arcs, e2_arcs, node.depth + 1),
            _node(fixed, no_forbidden, states, arcs, e2_arcs, node.depth + 1),
        )
    if kind == "state_used":
        sat_id, state_idx = key
        if (sat_id, state_idx) in states:
            return None
        yes_states = dict(states)
        yes_states[sat_id, state_idx] = 1
        no_states = dict(states)
        no_states[sat_id, state_idx] = 0
        return (
            _node(fixed, forbidden, yes_states, arcs, e2_arcs, node.depth + 1),
            _node(fixed, forbidden, no_states, arcs, e2_arcs, node.depth + 1),
        )
    if kind == "x":
        i, j, k = key
        if (i, j, k) in arcs:
            return None
        yes_arcs = dict(arcs)
        yes_arcs[i, j, k] = 1
        no_arcs = dict(arcs)
        no_arcs[i, j, k] = 0
        return (
            _node(fixed, forbidden, states, yes_arcs, e2_arcs, node.depth + 1),
            _node(fixed, forbidden, states, no_arcs, e2_arcs, node.depth + 1),
        )
    if kind == "e2_arc":
        sat_id, state_idx, i, j = key
        if (sat_id, state_idx, i, j) in e2_arcs:
            return None
        yes_e2 = dict(e2_arcs)
        yes_e2[sat_id, state_idx, i, j] = 1
        no_e2 = dict(e2_arcs)
        no_e2[sat_id, state_idx, i, j] = 0
        return (
            _node(fixed, forbidden, states, arcs, yes_e2, node.depth + 1),
            _node(fixed, forbidden, states, arcs, no_e2, node.depth + 1),
        )
    return None


def _node(fixed, forbidden, states, arcs, e2_arcs, depth: int) -> M4FixedGridBranchNode:
    return M4FixedGridBranchNode(
        fixed_assignment=tuple(sorted(fixed.items())),
        forbidden_assignments=tuple(sorted(forbidden)),
        fixed_state_use=tuple(sorted((sat_id, state_idx, value) for (sat_id, state_idx), value in states.items())),
        fixed_e1_arcs=tuple(sorted((i, j, k, value) for (i, j, k), value in arcs.items())),
        fixed_e2_arcs=tuple(sorted((sat_id, state_idx, i, j, value) for (sat_id, state_idx, i, j), value in e2_arcs.items())),
        depth=depth,
    )


def _program_key(program: M4SatelliteProgram) -> tuple:
    return (
        program.sat_id,
        float(program.ready_time),
        float(program.pickup_ready_time),
        tuple(tuple(route) for route in program.routes),
    )


def _result(status: str, objective: float | None, fixed_grid_proven_optimal: bool,
            nodes_processed: int, columns: list[M4SatelliteProgram], records: list[dict]):
    return M4FixedGridBranchPriceResult(
        status=status,
        proven_optimal=False,
        fixed_grid_proven_optimal=fixed_grid_proven_optimal,
        proof_scope="complete_branch_price_for_initial_m4_two_state_grid_only",
        objective=objective,
        nodes_processed=nodes_processed,
        columns=tuple(columns),
        node_records=tuple(records),
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
