"""ALNS state ownership, budget progress and acceptance draw contracts."""
from dataclasses import FrozenInstanceError
from importlib import import_module
import random

import pytest

from models.routing.alns.engine import Evaluation, OperatorWeights, Proposal, run_search
from models.routing.alns.search_control import ALNSParameters, SearchControl
from tests.routing.single_batch_fixtures import fixture
from tests.routing.test_model5_calendar import calendar_fixture


@pytest.mark.parametrize("number", range(1, 6))
def test_state_construction_and_cloning_keep_independent_route_lists(number):
    package = f"models.routing.model_{number}.alns"
    construction = import_module(package + ".construction")
    state_api = import_module(package + ".state")
    evaluator = import_module(package + ".evaluator")
    problem = fixture() if number < 5 else calendar_fixture().fixed_calendar(1)
    state = construction.initial_state(problem, random.Random(42))
    assert evaluator.initial_state(problem, random.Random(42)) == state
    before = state_api.state_key(state)
    cloned = state_api.clone_state(state)
    assert state_api.state_key(cloned) == before
    cloned["e2"][0]["tasks"].append(("extra", "d"))
    assert state_api.state_key(state) == before
    assert state_api.state_key(cloned) != before


@pytest.mark.parametrize("number", range(1, 6))
def test_acceptance_preserves_short_circuit_and_progress_uses_both_budgets(number):
    package = f"models.routing.model_{number}.alns"
    parameter_type = import_module(package + ".parameters").ALNSParameters
    control_type = import_module(package + ".search_control").SearchControl
    now = [0.0]
    parameters = parameter_type(max_time=10, max_iterations=100, seed=42)
    control = control_type(parameters, 0.0, lambda: now[0])
    assert control.progress(49) == pytest.approx(0.5)
    now[0] = 8.0
    assert control.progress(49) == pytest.approx(0.8)
    now[0] = 20.0
    assert control.progress(49) == 1.0

    class Rng:
        draws = 0

        def random(self):
            self.draws += 1
            return 0.0

    rng = Rng()
    assert control.accept(rng, 100, 99, 1)
    assert control.accept(rng, 100, 100, 1)
    assert rng.draws == 0
    assert control.accept(rng, 100, 101, 1)
    assert rng.draws == 1
    assert control.accept(rng, 100, 101, 1, joint=True)
    assert rng.draws == 2
    with pytest.raises(FrozenInstanceError):
        parameters.seed = 0


@pytest.mark.parametrize("credit_rejected_best, expected", [
    (True, {"destroy": 2.4, "repair": 1.6}),
    (False, {"destroy": 0.8, "repair": 0.8}),
])
def test_shared_search_records_incumbent_and_credits_paired_operators(credit_rejected_best, expected):
    control = SearchControl(ALNSParameters(10, 2, 42, segment_period=2), 0.0, lambda: 0.0)
    weights = OperatorWeights(control, ("destroy", "repair"), segmented=True,
                              credit_rejected_best=credit_rejected_best)
    start = Evaluation(10, "initial", None)
    candidate = Evaluation(11, "neighbor", "feasible")
    incumbents = []

    def propose(current, iteration):
        assert current is start
        if iteration == 0:
            return Proposal(current, candidate, ("destroy", "repair"))
        return Proposal(current, operators=("repair",))

    def remember(proposal, _iterations):
        incumbents.append(proposal.candidate)
        return True

    def accept(proposal, _iteration, improved):
        assert improved and incumbents == [proposal.candidate]
        return False

    assert run_search(start, control, weights, propose, remember, accept) == (start, 2, 0)
    assert weights.attempts == {"destroy": 1, "repair": 2}
    assert weights.improvements == {"destroy": 1, "repair": 1}
    assert weights.noops == {"destroy": 0, "repair": 1}
    assert weights.weights == pytest.approx(expected)
