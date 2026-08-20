#!/usr/bin/env python3
"""Regression for the validated M1 -> M7 time-expanded incumbent handoff.

The test intentionally uses the five-customer instance only.  It verifies
that a Model-1 route solution can be represented at its exact no-wait ready
state and remains a feasible M7 upper bound.  Neither side may call the
finite-pool solve a global M7 proof.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.instance import ProblemInstance
from models.model_1.solver import solve_exact as solve_m1_exact
from models.model_7.m1_incumbent_import import (
    import_m1_solution_as_m7_time_expanded_incumbent,
)
from models.model_7.time_expanded_route_master import solve_m7_time_expanded_pool
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model


EPS = 1e-6


def main() -> None:
    fleet = json.loads((ROOT / "config" / "fleet_config.json").read_text())
    nodes, _ = parse_dataset(str(ROOT / "data" / "5_customers" / "C101.csv"))
    problem = ProblemInstance(nodes, fleet, speed=float(fleet.get("speed", 1.0)))
    problem.aggregate_demands_from_pre_assignment()
    e1_tasks, e2_tasks = build_tasks_for_model(problem, 1)
    e1_routes, e2_routes, m1_info = solve_m1_exact(
        problem,
        e1_tasks,
        e2_tasks,
        problem.fleet.e2_per_sat_count,
        verbose=False,
        time_limit=60,
        return_info=True,
    )
    assert m1_info["proven_optimal"]

    imported = import_m1_solution_as_m7_time_expanded_incumbent(
        problem,
        e1_routes,
        e2_routes,
        source_info=m1_info,
    )
    assert imported["status"] == "VALIDATED_M1_IMPORT"
    assert not imported["proven_optimal"]
    assert not imported["global_integer_optimum_proven"]
    assert abs(imported["m7_feasible_upper_bound"] - m1_info["objective"]) <= EPS

    # Scoped import columns, with the matching ready-state family, reproduce
    # a feasible M7 restricted-master incumbent.  The pool's OPTIMAL status
    # is deliberately not a global M7 statement.
    pool = solve_m7_time_expanded_pool(
        problem,
        imported["route_columns"],
        problem.fleet.e2_per_sat_count,
        ready_states=imported["ready_states"],
        time_limit=60,
        verbose=False,
    )
    assert pool["info"]["objective"] is not None
    assert abs(pool["info"]["objective"] - imported["m7_feasible_upper_bound"]) <= EPS
    assert not pool["info"]["proven_optimal"]
    assert pool["info"]["proof_scope"] == "restricted_time_expanded_route_pool_only"
    print(f"M1 -> M7 time-expanded import: {imported['m7_feasible_upper_bound']:.6f} OK")


if __name__ == "__main__":
    main()
