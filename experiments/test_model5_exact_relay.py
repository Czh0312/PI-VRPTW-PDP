# -*- coding: utf-8 -*-
"""Regression tests for the exact one-handoff Model 5 formulation.

Run with the Python bundled with the local Gurobi installation, for example:

    C:\gurobi\win64\python311\bin\python.exe \
        experiments\test_model5_exact_relay.py

The test checks two contracts:

1. Disabling every relay makes Model 5 match Model 3's optimal objective.
2. A forced relay has a real carrier/receiver pair, valid chronology, and
   explicit load transfer at TP/TD/RP.
"""
import sys
from pathlib import Path

import gurobipy as gp
from gurobipy import GRB

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from core.instance import ProblemInstance
from core.node import Customer, Depot, Satellite
from models.model_3.solver import solve_exact as solve_m3_exact
from models.model_5.exact_joint import (
    build_model5_joint_exact,
    extract_solution,
)
from models.model_5.solver import (
    _concrete_handoff_patterns,
    _pickup_mode_patterns,
    solve_exact as solve_m5_exact,
)
from utils.task_builder import build_tasks_for_model


TOL = 1e-6


def _problem():
    nodes = {
        "D0": Depot("D0", 0.0, 0.0),
        "S0": Satellite("S0", 10.0, 0.0, service_time=1.0),
        "C0": Customer(
            "C0", 11.0, 0.0, demand=4.0, service_time=1.0,
            tw_del=(15.0, 80.0), tw_pick=(20.0, 160.0),
            assigned_sat="S0",
        ),
        "C1": Customer(
            "C1", 13.0, 0.0, demand=4.0, service_time=1.0,
            tw_del=(15.0, 80.0), tw_pick=(20.0, 160.0),
            assigned_sat="S0",
        ),
    }
    fleet = {
        "E1_vehicle_count": 1,
        "E1_Qk": 100.0,
        "E1_Ck": 20.0,
        "vehicles_per_satellite": 2,
        "E2_Qv": 20.0,
        "E2_Cv": 10.0,
        "container_weight": 2.0,
        "container_volume": 1.0,
        "speed": 1.0,
    }
    problem = ProblemInstance(nodes, fleet, speed=1.0)
    problem.aggregate_demands_from_pre_assignment()
    return problem


def _two_satellite_problem():
    nodes = {
        "D0": Depot("D0", 0.0, 0.0),
        "S0": Satellite("S0", 10.0, 0.0, service_time=1.0),
        "S1": Satellite("S1", -10.0, 0.0, service_time=1.0),
    }
    for customer_id, x, satellite in (
            ("C0", 11.0, "S0"), ("C1", 13.0, "S0"),
            ("C2", -11.0, "S1"), ("C3", -13.0, "S1")):
        nodes[customer_id] = Customer(
            customer_id, x, 0.0, demand=4.0, service_time=1.0,
            tw_del=(15.0, 80.0), tw_pick=(20.0, 160.0),
            assigned_sat=satellite,
        )
    fleet = {
        "E1_vehicle_count": 2,
        "E1_Qk": 100.0,
        "E1_Ck": 20.0,
        "vehicles_per_satellite": 2,
        "E2_Qv": 20.0,
        "E2_Cv": 10.0,
        "container_weight": 2.0,
        "container_volume": 1.0,
        "speed": 1.0,
    }
    problem = ProblemInstance(nodes, fleet, speed=1.0)
    problem.aggregate_demands_from_pre_assignment()
    return problem


def _incoming_value(z_values, node_id, vehicle, valid_nodes):
    return sum(
        z_values.get((i, node_id, vehicle), 0.0)
        for i in valid_nodes if i != node_id
    )


def _predecessor(z_values, node_id, vehicle, valid_nodes):
    return next(
        i for i in valid_nodes
        if i != node_id and z_values.get((i, node_id, vehicle), 0.0) > 0.5
    )


def test_no_relay_degenerates_to_model3():
    problem = _problem()
    e1_tasks, e2_tasks = build_tasks_for_model(problem, 3)

    _e1, _e2, m3_info = solve_m3_exact(
        problem, e1_tasks, e2_tasks,
        problem.fleet.e2_per_sat_count,
        TFp=9999.0, time_limit=30, verbose=False, return_info=True,
    )
    _e1, _e2, m5_info = solve_m5_exact(
        problem, e1_tasks, e2_tasks,
        problem.fleet.e2_per_sat_count,
        max_relocation_candidates=None,
        max_transshipments=0,
        warm_start_with_m3=False,
        time_limit=30, verbose=False, return_info=True,
    )
    _e1, _e2, canonical_info = solve_m5_exact(
        problem, e1_tasks, e2_tasks,
        problem.fleet.e2_per_sat_count,
        max_relocation_candidates=None,
        max_transshipments=None,
        warm_start_with_m3=False,
        time_limit=30, verbose=False, return_info=True,
    )

    assert m3_info["proven_optimal"] is True, m3_info
    assert m5_info["status"] == "OPTIMAL", m5_info
    assert abs(m3_info["objective"] - m5_info["objective"]) <= TOL, (
        m3_info, m5_info,
    )
    assert m5_info["transshipment_count"] == 0, m5_info
    assert m5_info["proven_optimal"] is False, (
        "The cap-0 M3 regression must not be labelled canonical Model 5",
        m5_info,
    )
    assert canonical_info["proven_optimal"] is True, canonical_info
    assert canonical_info["proof_scope"] == "full_single_handoff_model5"
    assert canonical_info["formulation"] == (
        "zero_pickup_load_relaxation_certificate"
    ), canonical_info
    assert canonical_info["objective"] <= m3_info["objective"] + TOL, (
        m3_info, canonical_info,
    )
    print(
        "M5 relay-off == M3: objective "
        f"{m5_info['objective']:.6f}"
    )


def test_forced_relay_has_physical_flow():
    problem = _problem()
    e1_tasks, e2_tasks = build_tasks_for_model(problem, 5)
    model, variables, ctx = build_model5_joint_exact(
        problem, e1_tasks, e2_tasks,
        problem.fleet.e2_per_sat_count,
        max_relocation_candidates=None,
    )

    forced_receive = "C0p@C1"
    for node_id, choose in variables["y"].items():
        info = ctx["e2_info"][node_id]
        if info.relocated_to != info.original_phys:
            model.addConstr(
                choose == int(node_id == forced_receive),
                f"Test_FixRelay_{node_id}",
            )

    model.setParam("OutputFlag", 0)
    model.setParam("TimeLimit", 30)
    model.setParam("IntFeasTol", 1e-9)
    model.setParam("FeasibilityTol", 1e-9)
    model.optimize()
    assert model.status == GRB.OPTIMAL, model.status

    _e1, _e2, relays = extract_solution(model, variables, ctx)
    assert len(relays) == 1, relays
    relay = relays[0]
    assert relay["pickup_task"] == "C0p", relay
    assert relay["origin"] == "C0" and relay["handoff"] == "C1", relay
    assert relay["carrier_vehicle"] != relay["receiver_vehicle"], relay
    assert relay["origin_pickup_time"] >= 20.0 - TOL, relay
    assert relay["origin_pickup_time"] <= 160.0 + TOL, relay
    assert relay["receiver_pickup_time"] >= relay["handoff_ready_time"] - TOL, relay

    satellite = relay["satellite"]
    carrier = (satellite, relay["carrier_vehicle"])
    receiver = (satellite, relay["receiver_vehicle"])
    tp_id = "TP@C0p@C1"
    td_id = "TD@C0p@C1"
    rp_id = forced_receive
    valid_nodes = [satellite] + ctx["e2_nodes_by_sat"][satellite]
    z_values = {key: var.X for key, var in variables["z"].items()}

    assert _incoming_value(z_values, tp_id, carrier, valid_nodes) > 0.5
    assert _incoming_value(z_values, td_id, carrier, valid_nodes) > 0.5
    assert _incoming_value(z_values, rp_id, receiver, valid_nodes) > 0.5

    tp_prev = _predecessor(z_values, tp_id, carrier, valid_nodes)
    td_prev = _predecessor(z_values, td_id, carrier, valid_nodes)
    rp_prev = _predecessor(z_values, rp_id, receiver, valid_nodes)
    weight = relay["weight"]
    volume = relay["volume"]

    assert abs(
        variables["Q2"][tp_id, carrier].X
        - variables["Q2"][tp_prev, carrier].X - weight
    ) <= TOL, (
        tp_prev, variables["Q2"][tp_prev, carrier].X,
        variables["Q2"][tp_id, carrier].X, weight,
    )
    assert abs(
        variables["Q2"][td_id, carrier].X
        - variables["Q2"][td_prev, carrier].X + weight
    ) <= TOL, (
        td_prev, variables["Q2"][td_prev, carrier].X,
        variables["Q2"][td_id, carrier].X, weight,
    )
    assert abs(
        variables["Q2"][rp_id, receiver].X
        - variables["Q2"][rp_prev, receiver].X - weight
    ) <= TOL
    assert abs(
        variables["V2"][tp_id, carrier].X
        - variables["V2"][tp_prev, carrier].X - volume
    ) <= TOL
    assert abs(
        variables["V2"][td_id, carrier].X
        - variables["V2"][td_prev, carrier].X + volume
    ) <= TOL
    assert abs(
        variables["V2"][rp_id, receiver].X
        - variables["V2"][rp_prev, receiver].X - volume
    ) <= TOL
    print(
        "Forced relay physical flow: "
        f"C0 --vehicle {relay['carrier_vehicle']}--> C1 "
        f"--vehicle {relay['receiver_vehicle']}--> S0"
    )


def test_complete_mode_partition_and_node_pruning():
    problem = _problem()
    e1_tasks, e2_tasks = build_tasks_for_model(problem, 5)
    patterns = _pickup_mode_patterns(e2_tasks)
    assert len(patterns) == 4, patterns
    assert {tuple(pattern) for _count, pattern in patterns} == {
        (("C0p", 0), ("C1p", 0)),
        (("C0p", 0), ("C1p", 1)),
        (("C0p", 1), ("C1p", 0)),
        (("C0p", 1), ("C1p", 1)),
    }
    both_relay = (("C0p", 1), ("C1p", 1))
    refinements = list(_concrete_handoff_patterns(both_relay, e2_tasks))
    assert refinements == [{"C0p": "C1", "C1p": "C0"}], refinements

    _model, _variables, ctx = build_model5_joint_exact(
        problem, e1_tasks, e2_tasks,
        problem.fleet.e2_per_sat_count,
        max_relocation_candidates=None,
        relay_fixes={"C0p": 1, "C1p": 0},
    )
    c0_candidates = ctx["pickup_candidate_nodes"]["C0p"]
    c1_candidates = ctx["pickup_candidate_nodes"]["C1p"]
    assert len(c0_candidates) == 1
    assert ctx["e2_info"][c0_candidates[0]].relocated_to == "C1"
    assert len(c1_candidates) == 1
    assert ctx["e2_info"][c1_candidates[0]].relocated_to == "C1"
    print("Complete mode partition and fixed-node pruning: OK")


def test_multisatellite_relay_candidates_never_cross_satellites():
    problem = _two_satellite_problem()
    e1_tasks, e2_tasks = build_tasks_for_model(problem, 5)
    _model, _variables, ctx = build_model5_joint_exact(
        problem, e1_tasks, e2_tasks,
        problem.fleet.e2_per_sat_count,
        max_relocation_candidates=None,
    )
    expected_customers = {"S0": {"C0", "C1"}, "S1": {"C2", "C3"}}
    for pickup_task, candidates in ctx["pickup_candidate_nodes"].items():
        candidate_satellites = {
            ctx["e2_info"][node_id].sat_id for node_id in candidates
        }
        candidate_customers = {
            ctx["e2_info"][node_id].relocated_to for node_id in candidates
        }
        task_satellite = ctx["e2_info"][candidates[0]].sat_id
        assert candidate_satellites == {task_satellite}, (
            pickup_task, candidate_satellites,
        )
        assert candidate_customers <= expected_customers[task_satellite], (
            pickup_task, candidate_customers,
        )

    try:
        build_model5_joint_exact(
            problem, e1_tasks, e2_tasks,
            problem.fleet.e2_per_sat_count,
            max_relocation_candidates=None,
            handoff_fixes={"C0p": "C2"},
        )
    except ValueError as exc:
        assert "同仓顾客点" in str(exc), exc
    else:
        raise AssertionError("跨前置仓交接点必须在建模阶段被拒绝")
    print("Multi-satellite relay candidates stay within their satellite: OK")


if __name__ == "__main__":
    test_no_relay_degenerates_to_model3()
    test_forced_relay_has_physical_flow()
    test_complete_mode_partition_and_node_pruning()
    test_multisatellite_relay_candidates_never_cross_satellites()
    print("Model 5 exact relay regression: OK")
