"""The explicit formulation context must describe the model actually built."""
from dataclasses import FrozenInstanceError
from importlib import import_module

import pytest

from tests.routing.test_single_batch_comparison import problem
from tests.routing.test_multibatch_cycle import fixture
from tests.routing.test_model6_uniform import uniform_fixture
from tests.routing.test_model78_calendar import calendar_fixture


def instance_for(number):
    if number < 5:
        return problem(number)
    if number == 5:
        return fixture()
    if number == 6:
        return uniform_fixture().for_count(2)
    return calendar_fixture(number)


@pytest.mark.parametrize("number", range(1, 9))
def test_public_builder_exposes_its_actual_parameters_and_variables(number):
    instance = instance_for(number)
    package = f"models.routing.model_{number}.exact"
    module = import_module(package + ".model")
    build = module.build_joint_model if number in (2, 4) else module.build_model
    model, context = build(instance, **({"batch_count": 2} if number >= 7 else {}))
    try:
        parameters = context["parameters"]
        variables = context["variables"]
        if number >= 7:
            # Public calendar entries create their own fixed-K projection.
            assert parameters.problem.network is instance.network
            instance = parameters.problem
        assert parameters.problem is instance
        assert parameters.policy is instance.operating_policy
        assert parameters.fleet is instance.fleet
        assert parameters.horizon == instance.operating_policy.cycle_end
        assert parameters.__class__.__module__ == package + ".parameters"
        assert variables.__class__.__module__ == package + ".variables"
        assert variables.model is model
        assert variables.routes["e1"] is context["e1"]
        assert variables.routes["e2"] is context["e2"]
        assert variables.assignments is context["assignments"]
        assert set(variables.task_times) == {
            (order.id, kind) for order in instance.orders for kind in instance.task_kinds
        }
        with pytest.raises(FrozenInstanceError):
            parameters.horizon = 0
        model.update()
        actual = model.getVars()
        for var in variables.task_times.values():
            assert var.sameAs(actual[var.index])
        for routes in variables.routes.values():
            for route in routes:
                assert route["used"].sameAs(actual[route["used"].index])
        assert model.NumQNZs == 0
        if number >= 6:
            assert variables.pool is context["pool"]
            assert variables.calendar["starts"] is context["batch_starts"]
            assert variables.calendar["cutoffs"] is context["cutoffs"]
            assert variables.physical_vehicles is context["physical_vehicles"]
    finally:
        model.dispose()
