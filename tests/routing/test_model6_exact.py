# -*- coding: utf-8 -*-
"""Regression tests for the compact homogeneous-container Model 6 MILP."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("gurobipy", reason="Model 6 exact tests require Gurobi")
from gurobipy import GRB

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.routing.test_model5_exact import _problem
from models.routing.variants.model_3.exact_solver import solve_exact as solve_m3_exact
from models.routing.variants.model_6.model import (
    build_model,
    extract_compact_solution,
)
from models.routing.variants.model_6.exact_solver import solve_exact as solve_m6_exact
from models.routing.common.task_builder import build_tasks_for_model


TOL = 1e-6


def _arc_sum(var_dict, node, vehicle, incoming):
    total = 0.0
    for (i, j, v), var in var_dict.items():
        if v != vehicle:
            continue
        if (incoming and j == node) or (not incoming and i == node):
            total += var.X
    return total


def test_no_transfer_solution_matches_model3():
    problem = _problem()
    e1_tasks, e2_tasks = build_tasks_for_model(problem, 6)
    _e1, _e2, m3 = solve_m3_exact(
        problem, e1_tasks, e2_tasks,
        problem.fleet.e2_per_sat_count,
        verbose=False, time_limit=30, return_info=True,
    )
    _e1, _e2, m6 = solve_m6_exact(
        problem, e1_tasks, e2_tasks,
        problem.fleet.e2_per_sat_count,
        verbose=False, time_limit=30, return_info=True,
    )
    assert m3["proven_optimal"] is True, m3
    assert m6["proven_optimal"] is True, m6
    assert m6["objective"] <= m3["objective"] + TOL, (m3, m6)
    assert m6["proof_scope"] == "compact_async_event_handoff_model6"
    print(f"M6 compact <= M3: {m6['objective']:.6f} <= {m3['objective']:.6f}")


def test_forced_transfer_moves_only_own_flow_into_relay_flow():
    problem = _problem()
    e1_tasks, e2_tasks = build_tasks_for_model(problem, 6)
    model, variables, ctx = build_model(
        problem, e1_tasks, e2_tasks,
        problem.fleet.e2_per_sat_count,
    )
    sender = ("S0", 0)
    receiver = ("S0", 1)
    event = "C1d"
    model.addConstr(variables["transfer"][event, sender, receiver] == 1)
    model.setParam("OutputFlag", 0)
    model.setParam("TimeLimit", 30)
    model.optimize()
    assert model.status == GRB.OPTIMAL, model.status

    _e1, routes, _vehicles, handoffs, total_boxes = extract_compact_solution(
        model, variables, ctx
    )
    assert total_boxes == 1, handoffs
    assert len(handoffs) == 1, handoffs
    handoff = handoffs[0]
    assert {
        key: handoff[key]
        for key in (
            "satellite", "event", "physical_location", "sender_vehicle",
            "receiver_vehicle", "quantity",
        )
    } == {
        "satellite": "S0",
        "event": "C1d",
        "physical_location": "C1",
        "sender_vehicle": 0,
        "receiver_vehicle": 1,
        "quantity": 1,
    }
    assert handoff["receiver_pickup_time"] + TOL >= handoff["handoff_ready_time"]

    sender_own_in = _arc_sum(variables["own"], event, sender, True)
    sender_own_out = _arc_sum(variables["own"], event, sender, False)
    receiver_relay_in = _arc_sum(variables["relay"], event, receiver, True)
    receiver_relay_out = _arc_sum(variables["relay"], event, receiver, False)
    assert abs(sender_own_in - sender_own_out - 1.0) <= TOL
    assert abs(receiver_relay_out - receiver_relay_in - 1.0) <= TOL
    assert any("MEET@C1d" in route for route in routes["S0"]), routes
    print("Forced handoff: own flow decreases and relay flow increases exactly once")


if __name__ == "__main__":
    test_no_transfer_solution_matches_model3()
    test_forced_transfer_moves_only_own_flow_into_relay_flow()
    print("Model 6 compact exact regression: OK")
