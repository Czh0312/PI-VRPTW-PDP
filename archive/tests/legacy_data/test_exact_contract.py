# -*- coding: utf-8 -*-
"""Smoke-test the certified-exact result contract on a small instance.

Run with:
    .venv/bin/python experiments/test_exact_contract.py

The test intentionally checks metadata rather than objective constants, so it
remains valid when a corrected formulation changes the numerical optimum.
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from models.routing.common.domain.instance import ProblemInstance
from models.routing.variants.model_1.solver import solve_exact as solve_m1
from models.routing.variants.model_3.solver import solve_exact as solve_m3
from models.routing.variants.model_4.exact import solve_exact as solve_m4
from models.routing.variants.model_2.exact import solve_exact as solve_m2
from models.routing.variants.model_3.route_universe import solve_route_universe_exact
from models.routing.variants.model_3.route_master import (
    solve_route_master_exact,
    solve_route_master_pool,
)
from models.routing.variants.model_3.route_pricing import solve_e2_route_pricing
from models.routing.common.data_loader import parse_dataset
from models.routing.common.task_builder import build_tasks_for_model


def _problem():
    fleet = json.loads(
        (PROJECT_ROOT / "config" / "routing" / "validation" / "zhou_small.json").read_text(
            encoding="utf-8"
        )
    )
    nodes, _ = parse_dataset(str(PROJECT_ROOT / "data" / "5_customers" / "C101.csv"))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def _assert_certified(name, solve, model, **kwargs):
    problem = _problem()
    e1_tasks, e2_tasks = build_tasks_for_model(problem, model)
    e1_routes, e2_routes, info = solve(
        problem, e1_tasks, e2_tasks, problem.fleet.e2_per_sat_count,
        time_limit=60, verbose=False, return_info=True, **kwargs,
    )
    assert e1_routes and e2_routes, f"{name}: missing routes: {info}"
    assert info["proven_optimal"] is True, f"{name}: not certified: {info}"
    assert info["solution_type"] == "proven_optimal", info
    print(f"{name}: certified objective={info['objective']:.6f}")
    return info


if __name__ == "__main__":
    m1_info = _assert_certified("M1", solve_m1, 1)
    m3_info = _assert_certified("M3", solve_m3, 2, TFp=9999.0)

    problem = _problem()
    _, _, _, m4_info = solve_m4(
        problem, problem.fleet.e2_per_sat_count,
        time_limit=60, verbose=False, return_info=True,
    )
    assert m4_info["proven_optimal"] is True, m4_info
    assert m4_info["solution_type"] == "proven_optimal", m4_info
    assert m4_info["proof_scope"] == "full_arc_flow", m4_info
    assert abs(m4_info["objective"] - m3_info["objective"]) <= 1e-6, (
        m3_info, m4_info,
    )
    print("M4 arc-flow: certified and matches M3 on the fixed-assignment case")

    problem = _problem()
    _, _, _, m2_info = solve_m2(
        problem, problem.fleet.e2_per_sat_count,
        time_limit=60, verbose=False, return_info=True,
    )
    assert m2_info["proven_optimal"] is True, m2_info
    assert m2_info["solution_type"] == "proven_optimal", m2_info
    assert m2_info["objective"] <= m1_info["objective"] + 1e-6, (
        m1_info, m2_info,
    )
    print("M2 arc-flow: certified and no worse than M1 fixed assignment")

    problem = _problem()
    e1_tasks, e2_tasks = build_tasks_for_model(problem, 3)
    e1_routes, e2_routes, route_info = solve_route_universe_exact(
        problem, e1_tasks, e2_tasks, problem.fleet.e2_per_sat_count,
        TFp=9999.0, time_limit=60, verbose=False, return_info=True,
    )
    assert e1_routes and e2_routes, route_info
    assert route_info["proven_optimal"] is True, route_info
    assert abs(route_info["objective"] - m3_info["objective"]) <= 1e-6, (
        m3_info, route_info,
    )
    print("M3 route universe: certified and matches arc-flow optimum")

    e1_routes, e2_routes, master_info = solve_route_master_exact(
        problem, e1_tasks, e2_tasks, problem.fleet.e2_per_sat_count,
        TFp=9999.0, time_limit=60, verbose=False, return_info=True,
    )
    assert e1_routes and e2_routes, master_info
    assert master_info["proven_optimal"] is True, master_info
    assert abs(master_info["objective"] - m3_info["objective"]) <= 1e-6, (
        m3_info, master_info,
    )
    print("M3 direct route master: certified and matches arc-flow optimum")

    _, _, pool_info = solve_route_master_pool(
        problem, e1_tasks, e2_tasks, problem.fleet.e2_per_sat_count,
        e2_routes, TFp=9999.0, time_limit=60, verbose=False,
        return_info=True,
    )
    assert pool_info["status"] == "OPTIMAL", pool_info
    assert pool_info["proven_optimal"] is False, pool_info
    assert pool_info["solution_type"] == "restricted_route_pool_optimal", pool_info
    assert pool_info["route_universe_complete"] is False, pool_info
    print("M3 route-pool master: correctly non-certified")

    _, _, branch_info = solve_route_master_exact(
        problem, e1_tasks, e2_tasks, problem.fleet.e2_per_sat_count,
        TFp=9999.0, time_limit=60, verbose=False, return_info=True,
        branch_rules={"same": [("C1d", "C0d")]},
    )
    assert branch_info["status"] == "OPTIMAL", branch_info
    assert branch_info["solution_type"] == "branch_node_optimal", branch_info
    assert branch_info["proven_optimal"] is False, branch_info
    print("M3 Ryan-Foster branch node: correctly non-certified")

    priced = solve_e2_route_pricing(
        problem, "S0", e2_tasks["S0"], time_limit=30,
        same_pairs=[("C0d", "C1d")],
    )
    assert priced["status"] == "OPTIMAL" and priced["route"] is not None, priced
    path = priced["route"].path
    assert ("C0d" in path) == ("C1d" in path), path
    print("M3 E2 pricing oracle: branch rule respected")
    print("exact-result contract: OK")
