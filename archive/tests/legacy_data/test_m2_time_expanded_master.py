"""Small exactness regression for the M2 time-expanded route-master pieces.

This test does *not* claim that a finite route pool is globally exact.  It
uses the complete elementary route universe only for the five-customer smoke
instance, where exhaustive enumeration is practical, to verify the
time-expanded formulation and its pricing dual contract.
"""

from __future__ import annotations

import json
import sys
from itertools import permutations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.routing.common.domain.instance import ProblemInstance
from models.routing.variants.model_2.certified_time_expansion import (
    ReadyState,
    canonicalize_ready_states,
    enumerate_canonical_ready_states,
    enumerate_ready_states,
)
from models.routing.variants.model_2.exact import solve_exact
from models.routing.variants.model_2.time_expanded_pricing import price_m2_ready_state
from models.routing.variants.model_2.time_expanded_column_generation import (
    singleton_seed_columns,
    solve_m2_time_expanded_root_cg,
)
from models.routing.variants.model_2.time_expanded_route_master import (
    audit_time_expanded_reduced_costs,
    build_m2_time_expanded_master,
    extract_time_expanded_pricing_duals,
    solve_m2_time_expanded_pool,
)
from models.routing.common.data_loader import parse_dataset


EPS = 1e-6


def _load_problem() -> ProblemInstance:
    fleet = json.loads((ROOT / "config" / "routing" / "validation" / "zhou_small.json").read_text())
    nodes, _ = parse_dataset(str(ROOT / "data" / "5_customers" / "C101.csv"))
    return ProblemInstance(nodes, fleet, speed=float(fleet.get("speed", 1.0)))


def _complete_routes(problem: ProblemInstance) -> list[dict]:
    """All nonempty elementary paths for the one-satellite 5c smoke case."""

    if len(problem.satellites) != 1:
        raise AssertionError("the smoke instance must have exactly one satellite")
    sat_id = problem.satellites[0].id
    tasks = tuple(f"{customer.id}d" for customer in problem.customers)
    return [
        {"sat_id": sat_id, "path": (sat_id,) + route + (sat_id,), "source": "complete_5c"}
        for size in range(1, len(tasks) + 1)
        for route in permutations(tasks, size)
    ]


def _singleton_routes(problem: ProblemInstance) -> list[dict]:
    sat_id = problem.satellites[0].id
    return [
        {"sat_id": sat_id, "path": (sat_id, f"{customer.id}d", sat_id), "source": "singleton"}
        for customer in problem.customers
    ]


def _solve_lp_and_duals(problem: ProblemInstance, columns: list[dict]):
    model, variables, context = build_m2_time_expanded_master(
        problem, columns, problem.fleet.e2_per_sat_count, relax=True
    )
    model.setParam("OutputFlag", 0)
    model.optimize()
    if model.status != 2:  # GRB.OPTIMAL; avoid importing Gurobi in this test.
        raise AssertionError(f"restricted LP did not solve: status={model.status}")
    audit_time_expanded_reduced_costs(model, variables, context)
    return model, context, extract_time_expanded_pricing_duals(model, context)


def main() -> None:
    problem = _load_problem()
    complete_routes = _complete_routes(problem)

    # The opt-in canonical generator merges only *exactly* equal h values and
    # selects a stable diagnostic prefix.  Near-equal values must remain
    # distinct: unlike exact equality, they do not have an equivalence proof
    # at a tight delivery deadline.
    synthetic = canonicalize_ready_states({
        "S": (
            ReadyState("S", 5.0, ("D", "B", "S")),
            ReadyState("S", 5.0, ("D", "A", "S")),
            ReadyState("S", 5.000000001, ("D", "C", "S")),
        )
    })
    if len(synthetic["S"]) != 2 or synthetic["S"][0].prefix != ("D", "A", "S"):
        raise AssertionError(f"exact-time canonicalisation is unstable: {synthetic}")

    full_states = enumerate_ready_states(problem)
    canonical_states = enumerate_canonical_ready_states(problem)
    if sum(map(len, canonical_states.values())) > sum(map(len, full_states.values())):
        raise AssertionError("canonical ready-state family grew unexpectedly")

    # (1) Complete 5c universe: time-expanded integer master equals the full
    # arc-flow MIP.  Only the arc-flow status provides the separate global
    # proof; the pool solver stays explicitly restricted in its metadata.
    pool_result = solve_m2_time_expanded_pool(
        problem, complete_routes, problem.fleet.e2_per_sat_count, time_limit=60
    )
    _, _, _, arc_info = solve_exact(
        problem, problem.fleet.e2_per_sat_count, time_limit=60,
        verbose=False, return_info=True,
    )
    if pool_result["info"]["status"] != "OPTIMAL" or not arc_info["proven_optimal"]:
        raise AssertionError(f"5c solve failed: pool={pool_result['info']}, arc={arc_info}")
    if abs(pool_result["info"]["objective"] - arc_info["objective"]) > EPS:
        raise AssertionError(
            "time-expanded complete universe disagrees with arc-flow: "
            f"{pool_result['info']['objective']} vs {arc_info['objective']}"
        )
    if pool_result["info"]["proven_optimal"]:
        raise AssertionError("a finite time-expanded pool must not self-certify")

    # The canonical family is an opt-in fresh-run representation.  On the
    # five-customer instance it reaches the same complete-universe MIP value
    # as both the legacy state family and the full arc-flow formulation.
    canonical_pool = solve_m2_time_expanded_pool(
        problem,
        complete_routes,
        problem.fleet.e2_per_sat_count,
        ready_states=canonical_states,
        time_limit=60,
    )
    if canonical_pool["info"]["status"] != "OPTIMAL" or abs(
        canonical_pool["info"]["objective"] - arc_info["objective"]
    ) > EPS:
        raise AssertionError(
            "canonical time family disagrees with arc-flow: "
            f"{canonical_pool['info']} vs {arc_info}"
        )

    # (2) At the LP optimum of the complete universe, pricing every ready
    # state must prove the absence of a negative omitted route.
    _, complete_ctx, complete_duals = _solve_lp_and_duals(problem, complete_routes)
    for sat_id in complete_ctx["F"]:
        for state_idx, state in enumerate(complete_ctx["states"][sat_id]):
            known_complete_paths = {
                column.path
                for column in complete_ctx["columns"]
                if column.sat_id == sat_id and column.state_idx == state_idx
            }
            pricing = price_m2_ready_state(
                problem,
                sat_id,
                state.ready_time,
                state_idx=state_idx,
                cover_duals=complete_duals["cover"],
                vehicle_dual=complete_duals["vehicle"][sat_id],
                state_use_dual=complete_duals["state_use"][sat_id, state_idx],
                state_customer_duals={
                    customer_id: complete_duals["state_customer"][
                        customer_id, sat_id, state_idx
                    ]
                    for customer_id in complete_ctx["C"]
                },
                excluded_paths=known_complete_paths,
            )
            if not pricing.proven_no_negative_column:
                raise AssertionError(
                    f"complete-universe omitted-route pricing found/failed at {(sat_id, state_idx)}: {pricing}"
                )

    # (3) A singleton-only pool has an omitted improving route, proving that
    # reduced costs are actually being checked rather than assumed closed.
    _, singleton_ctx, singleton_duals = _solve_lp_and_duals(problem, _singleton_routes(problem))
    negative_found = False
    first_negative_columns = None
    for sat_id in singleton_ctx["F"]:
        for state_idx, state in enumerate(singleton_ctx["states"][sat_id]):
            known_singleton_paths = {
                column.path
                for column in singleton_ctx["columns"]
                if column.sat_id == sat_id and column.state_idx == state_idx
            }
            pricing = price_m2_ready_state(
                problem,
                sat_id,
                state.ready_time,
                state_idx=state_idx,
                cover_duals=singleton_duals["cover"],
                vehicle_dual=singleton_duals["vehicle"][sat_id],
                state_use_dual=singleton_duals["state_use"][sat_id, state_idx],
                state_customer_duals={
                    customer_id: singleton_duals["state_customer"][
                        customer_id, sat_id, state_idx
                    ]
                    for customer_id in singleton_ctx["C"]
                },
                excluded_paths=known_singleton_paths,
            )
            negative_found |= bool(pricing.columns)
            if any(tuple(column["path"]) in known_singleton_paths for column in pricing.columns):
                raise AssertionError("pricing no-good cuts returned an existing RMP route")
            if first_negative_columns is None and pricing.columns:
                first_negative_columns = list(pricing.columns)
    if not negative_found:
        raise AssertionError("singleton pool unexpectedly has no negative priced route")

    # The priced path is scoped to one ready state.  Adding it must improve
    # the RMP; silently expanding it to every state would break this reduced-
    # cost contract in a degenerate master.
    singleton_model, _, _ = build_m2_time_expanded_master(
        problem, _singleton_routes(problem), problem.fleet.e2_per_sat_count,
        relax=True,
    )
    singleton_model.setParam("OutputFlag", 0)
    singleton_model.optimize()
    augmented_model, _, _ = build_m2_time_expanded_master(
        problem,
        _singleton_routes(problem) + first_negative_columns,
        problem.fleet.e2_per_sat_count,
        relax=True,
    )
    augmented_model.setParam("OutputFlag", 0)
    augmented_model.optimize()
    if augmented_model.ObjVal >= singleton_model.ObjVal - EPS:
        raise AssertionError(
            "state-scoped negative pricing column failed to improve the RMP"
        )

    # (4) The global root-CG driver closes the same LP only after every ready
    # state prices out.  This is still a lower-bound proof, not an integer
    # branch-price certificate.
    root_cg = solve_m2_time_expanded_root_cg(
        problem,
        problem.fleet.e2_per_sat_count,
        initial_route_columns=singleton_seed_columns(problem),
        ready_states=canonical_states,
        max_iterations=10,
        pricing_time_limit=10.0,
        pricing_pool_solutions=20,
    )
    if not root_cg["root_lp_priced_complete"]:
        raise AssertionError(f"root CG did not close: {root_cg}")
    if root_cg["proven_optimal"]:
        raise AssertionError("root LP closure must not claim an integer proof")
    if abs(root_cg["root_lp_lower_bound"] - arc_info["objective"]) > EPS:
        raise AssertionError(f"root-CG bound mismatch: {root_cg} vs {arc_info}")

    print("M2 time-expanded master regression passed")
    print(f"  objective: {arc_info['objective']:.10f}")
    print(f"  complete raw paths: {len(complete_routes)}")


if __name__ == "__main__":
    main()
