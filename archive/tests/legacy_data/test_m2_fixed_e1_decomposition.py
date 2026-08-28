#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for the fixed-E1 M2 route-universe oracle.

The 5/10-customer C101 instances have one satellite, so free assignment has
no remaining freedom.  A complete fixed-skeleton M2 universe must therefore
match the already exact Model-1 formulation.  The oracle is intentionally a
conditional result and must not mark itself as a global M2 proof.
"""

import json
import sys
from pathlib import Path

from gurobipy import GRB


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.routing.common.domain.instance import ProblemInstance
from models.routing.variants.model_1.solver import solve_exact as solve_m1_exact
from models.routing.variants.model_2.fixed_e1_decomposition import (
    build_fixed_e1_context,
    build_fixed_e1_route_master,
    enumerate_m2_routes_for_fixed_skeleton,
    solve_fixed_e1_skeleton_root_cg,
    solve_fixed_e1_skeleton_complete_universe,
)
from models.routing.common.data_loader import parse_dataset
from models.routing.common.task_builder import build_tasks_for_model


def _problem(scale):
    config = json.loads((ROOT / "config" / "routing" / "validation" / "zhou_small.json").read_text())
    nodes, _ = parse_dataset(str(ROOT / "data" / scale / "C101.csv"))
    problem = ProblemInstance(nodes, config, speed=config.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def _check(scale):
    problem = _problem(scale)
    assert len(problem.satellites) == 1, "test requires the one-satellite C101 case"
    e1_tasks, e2_tasks = build_tasks_for_model(problem, 1)
    _, _, m1_info = solve_m1_exact(
        problem,
        e1_tasks,
        e2_tasks,
        problem.fleet.e2_per_sat_count,
        verbose=False,
        time_limit=60,
        return_info=True,
    )
    fixed = solve_fixed_e1_skeleton_complete_universe(
        problem,
        [[problem.satellites[0].id]],
        max_tasks_per_satellite=10,
        time_limit=60,
    )
    assert m1_info["proven_optimal"]
    assert fixed["route_universe_complete"]
    assert fixed["fixed_skeleton_proven_optimal"]
    assert not fixed["proven_optimal"], "a fixed skeleton is not global M2 proof"
    assert abs(fixed["objective"] - m1_info["objective"]) <= 1e-6

    # The root CG must close the same conditional *LP* as the complete route
    # universe.  It is deliberately not allowed to report an integer or
    # global proof; its restricted-pool IP is only an incumbent generator.
    skeleton = [[problem.satellites[0].id]]
    context = build_fixed_e1_context(problem, skeleton)
    routes_by_sat, complete, reason = enumerate_m2_routes_for_fixed_skeleton(
        problem, context, max_tasks_per_satellite=10,
    )
    assert complete, reason
    universe_lp, _, _ = build_fixed_e1_route_master(
        problem, context, routes_by_sat, relax=True,
    )
    universe_lp.Params.OutputFlag = 0
    universe_lp.optimize()
    assert universe_lp.Status == GRB.OPTIMAL

    root_cg = solve_fixed_e1_skeleton_root_cg(
        problem,
        skeleton,
        max_iterations=100,
        time_limit=60,
        pricing_time_limit=30,
        pricing_pool_solutions=10,
        restricted_ip_time_limit=30,
    )
    assert root_cg["fixed_skeleton_root_lp_proven"]
    assert not root_cg["proven_optimal"]
    assert not root_cg["fixed_skeleton_proven_optimal"]
    assert abs(root_cg["artificial_sum"]) <= 1e-7
    assert abs(root_cg["lp_objective_without_artificials"] - universe_lp.ObjVal) <= 1e-6
    assert max(
        check["max_absolute_error"]
        for check in root_cg["dual_reduced_cost_checks"]
    ) <= 1e-6
    assert not root_cg["restricted_ip_incumbent"]["is_fixed_skeleton_integer_proof"]
    assert not root_cg["restricted_ip_incumbent"]["is_global_proof"]
    print(f"{scale}: {fixed['objective']:.6f} OK")


def main():
    _check("5_customers")
    _check("10_customers")


if __name__ == "__main__":
    main()
