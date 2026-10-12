"""ALNS state ownership, budget progress and acceptance draw contracts."""
from dataclasses import FrozenInstanceError
from importlib import import_module
import random

import pytest

from tests.routing.test_multibatch_cycle import fixture


@pytest.mark.parametrize("number", range(1, 9))
def test_state_construction_and_cloning_keep_independent_route_lists(number):
    package = f"models.routing.model_{number}.alns"
    construction = import_module(package + ".construction")
    state_api = import_module(package + ".state")
    evaluator = import_module(package + ".evaluator")
    problem = fixture(one_batch=True)
    state = construction.initial_state(problem, random.Random(42))
    assert evaluator.initial_state(problem, random.Random(42)) == state
    before = state_api.state_key(state)
    cloned = state_api.clone_state(state)
    assert state_api.state_key(cloned) == before
    cloned["e2"][0]["tasks"].append(("extra", "d"))
    assert state_api.state_key(state) == before
    assert state_api.state_key(cloned) != before


@pytest.mark.parametrize("number", range(1, 9))
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
