"""Contract tests for the unified ALNS benchmark runner."""

from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Customer, Depot, Satellite

from experiments.runs.run_model12346_alns_scaling import (
    fixed_assignment_early_due_conflicts,
)


def _problem(delivery_due):
    nodes = {
        "D0": Depot("D0", 0.0, 0.0),
        "S0": Satellite("S0", 10.0, 0.0),
        "C0": Customer(
            "C0", 20.0, 0.0, demand=1.0, service_time=0.0,
            tw_del=(0.0, delivery_due), tw_pick=(30.0, 60.0),
            assigned_sat="S0",
        ),
    }
    fleet = {
        "E1_vehicle_count": 1,
        "E1_Qk": 20.0,
        "E1_Ck": 20.0,
        "vehicles_per_satellite": 1,
        "E2_Qv": 10.0,
        "E2_Cv": 10.0,
        "container_weight": 1.0,
        "container_volume": 1.0,
        "speed": 1.0,
    }
    problem = ProblemInstance(nodes, fleet, speed=1.0)
    problem.aggregate_demands_from_pre_assignment()
    return problem


def test_fixed_assignment_direct_travel_conflict_is_proof_safe():
    conflicts = fixed_assignment_early_due_conflicts(_problem(15.0))

    assert len(conflicts) == 1
    assert conflicts[0]["customer"] == "C0"
    assert conflicts[0]["assigned_satellite"] == "S0"
    assert conflicts[0]["earliest_direct_delivery"] == 20.0
    assert conflicts[0]["lateness_lower_bound"] == 5.0


def test_fixed_assignment_direct_travel_allows_nonconflicting_due_time():
    assert fixed_assignment_early_due_conflicts(_problem(25.0)) == []
