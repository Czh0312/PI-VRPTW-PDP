"""5-customer safety regression for external M7 time-expanded branch-price.

The test intentionally exercises two contracts:

1. A full priced tree may be certified only when it closes; on this tiny
   instance the root LP itself is integral, so the one-node tree closes.
2. An intentionally insufficient node-CG budget must remain non-certified.

It is not a 50-customer claim or benchmark.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.instance import ProblemInstance
from models.model_7.certified_time_expansion import enumerate_ready_states
from models.model_7.exact import solve_exact
from models.model_7.time_expanded_branch_price import (
    _BranchNode,
    _choose_branch,
    _deserialise_node,
    _price_node_to_completion,
    _serialise_node,
    _seed_columns,
    solve_m7_time_expanded_branch_price,
)
from models.model_7.time_expanded_column_generation import singleton_seed_columns
from utils.data_loader import parse_dataset


EPS = 1e-6


class _Value:
    """Minimal variable stand-in for a pure branch-choice regression."""

    def __init__(self, value: float):
        self.X = value


def _load_problem() -> ProblemInstance:
    fleet = json.loads((ROOT / "config" / "fleet_config.json").read_text())
    nodes, _ = parse_dataset(str(ROOT / "data" / "5_customers" / "C101.csv"))
    return ProblemInstance(nodes, fleet, speed=float(fleet.get("speed", 1.0)))


def _assert_rf_child_pricing(problem: ProblemInstance) -> None:
    """Exercise both RF child types through the same controller primitive.

    This is deliberately a small internal regression: the public controller's
    full tree closes at the integer root for C101, so it would otherwise not
    execute a Ryan--Foster row.  A ``same`` child begins without a together
    seed route and must use/clear its phase-I RF artificial before its bound
    is accepted.  A ``different`` child must forbid together columns in
    pricing while still closing the node LP.
    """

    pair = tuple(sorted((problem.customers[0].id, problem.customers[1].id)))
    states = enumerate_ready_states(problem)
    outcomes = {}
    for relation in ("same", "different"):
        raw_columns, seen_unscoped, seen_state = _seed_columns(
            singleton_seed_columns(problem)
        )
        outcome = _price_node_to_completion(
            problem,
            problem.fleet.e2_per_sat_count,
            states,
            _BranchNode(node_id=1, depth=1, relations={pair: relation}),
            raw_columns,
            seen_unscoped,
            seen_state,
            max_cg_iterations=12,
            pricing_time_limit=10.0,
            pricing_pool_solutions=20,
            artificial_cost=1_000_000.0,
            lp_method=None,
            lp_crossover=None,
            deadline=None,
            output_flag=False,
            eps=EPS,
        )
        if not outcome["complete"]:
            raise AssertionError(f"RF {relation} child did not price closed: {outcome}")
        if outcome["record"]["cg_iterations"][-1]["artificial_value"] > EPS:
            raise AssertionError(f"RF {relation} child accepted a phase-I artificial")
        outcomes[relation] = outcome

    if not any(
        iteration["rf_artificial_value"] > EPS
        for iteration in outcomes["same"]["record"]["cg_iterations"]
    ):
        raise AssertionError("same RF child did not expose its missing-pair phase-I gap")


def _assert_ready_state_partition_choice() -> None:
    """Balanced ready-state branching is exact and checkpoint-round-trippable."""

    variables = {
        "w": {},
        "served_s": {"S0": _Value(1.0)},
        "x": {},
        "y_lin": {},
        "ready_state": {("S0", 0): _Value(0.5), ("S0", 1): _Value(0.5)},
        "lambda": {},
    }
    context = {"F": ("S0",), "states": {"S0": (object(), object())}, "C": (), "columns": ()}
    choice = _choose_branch(variables, context, _BranchNode(node_id=0, depth=0), EPS)
    if choice["kind"] != "ready_state_partition":
        raise AssertionError(f"expected balanced ready-state partition, got {choice}")
    if choice["satellite"] != "S0" or len(choice["state_indices"]) != 1:
        raise AssertionError(f"unexpected ready-state partition: {choice}")
    node = _BranchNode(
        node_id=3,
        depth=2,
        state_partitions={("S0", tuple(choice["state_indices"])): 1},
    )
    recovered = _deserialise_node(_serialise_node(node))
    if recovered.state_partitions != node.state_partitions:
        raise AssertionError("ready-state partition checkpoint round-trip failed")


def main() -> None:
    problem = _load_problem()
    _, _, _, arc_info = solve_exact(
        problem,
        problem.fleet.e2_per_sat_count,
        time_limit=60,
        verbose=False,
        return_info=True,
    )
    if not arc_info["proven_optimal"]:
        raise AssertionError(f"arc-flow smoke baseline failed: {arc_info}")

    # The controller starts from singleton columns, completes phase I,
    # prices every state at the root, and sees an integral root solution.
    # It may mark exact only because its queue is empty afterward.
    proof = solve_m7_time_expanded_branch_price(
        problem,
        problem.fleet.e2_per_sat_count,
        max_nodes=32,
        max_cg_iterations_per_node=12,
        pricing_time_limit=10.0,
        pricing_pool_solutions=20,
    )
    if not proof["proven_optimal"] or not proof["branch_and_price_complete"]:
        raise AssertionError(f"expected closed 5c branch-price tree: {proof}")
    if proof["processed_nodes"] != len(proof["nodes"]):
        raise AssertionError("every processed branch node must be recorded")
    if proof["unresolved_nodes"]:
        raise AssertionError(f"closed tree contains unresolved nodes: {proof}")
    if abs(proof["objective"] - arc_info["objective"]) > EPS:
        raise AssertionError(
            f"branch-price disagrees with arc exact: {proof['objective']} vs {arc_info['objective']}"
        )

    _assert_rf_child_pricing(problem)
    _assert_ready_state_partition_choice()

    # One iteration is only enough to leave phase I and rebuild the original
    # LP, not to price it.  A false exact flag here would invalidate the
    # entire certificate contract.
    incomplete = solve_m7_time_expanded_branch_price(
        problem,
        problem.fleet.e2_per_sat_count,
        max_nodes=8,
        max_cg_iterations_per_node=1,
        pricing_time_limit=10.0,
    )
    if incomplete["proven_optimal"] or incomplete["branch_and_price_complete"]:
        raise AssertionError(f"incomplete branch-price run self-certified: {incomplete}")
    if not incomplete["unresolved_nodes"]:
        raise AssertionError("incomplete run must report why its tree is open")

    # A production-sized B&P run must be interruptible at a safe node
    # boundary.  The wall-clock cap may never turn an unprocessed tree into a
    # certificate simply because no MIP solve was launched.
    time_capped = solve_m7_time_expanded_branch_price(
        problem,
        problem.fleet.e2_per_sat_count,
        wall_time_limit=1e-12,
    )
    if time_capped["proven_optimal"] or time_capped["branch_and_price_complete"]:
        raise AssertionError("wall-clock capped B&P run self-certified")
    if not any(item.get("reason") == "global_wall_time_limit"
               for item in time_capped["unresolved_nodes"]):
        raise AssertionError(f"missing global wall-time stop reason: {time_capped}")
    if [node["node_id"] for node in time_capped["pending_nodes"]] != [0]:
        raise AssertionError("time-capped root checkpoint lost or duplicated its pending node")

    resumed = solve_m7_time_expanded_branch_price(
        problem,
        problem.fleet.e2_per_sat_count,
        initial_route_columns=time_capped["raw_route_columns"],
        resume_state=time_capped,
        max_nodes=32,
        max_cg_iterations_per_node=12,
        pricing_time_limit=10.0,
        pricing_pool_solutions=20,
    )
    if not resumed["proven_optimal"] or not resumed["resumed_from_checkpoint"]:
        raise AssertionError(f"checkpoint resume did not close the 5c tree: {resumed}")
    if resumed["processed_nodes"] != len(resumed["nodes"]):
        raise AssertionError("resumed tree's processed-node count is inconsistent")

    print("M7 time-expanded external branch-price regression passed")
    print(f"  objective: {proof['objective']:.10f}")
    print(f"  closed nodes: {proof['processed_nodes']}")


if __name__ == "__main__":
    main()
