"""Necessary physical inequalities preserve optima and strengthen stock relaxations."""
from dataclasses import replace

import gurobipy as gp
import pytest

from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import CalendarCycleInstance
from models.routing.model_8.exact import solve_exact
from models.routing.model_8.exact.model import build_model
from tests.routing.test_model6_joint import joint_fixture
from tests.routing.test_model78_calendar import calendar_fixture
from tests.routing.test_productive_batches import productive_fixture


def instance(kind):
    if kind == 'ordinary':
        return calendar_fixture(8, counts=(1, 2, 3))
    if kind == 'late_release':
        return calendar_fixture(8, counts=(1, 2, 3), late_release=True)
    if kind == 'multiple_satellites':
        return productive_fixture(8, count=2, orders=2, two_sites=True)
    source = joint_fixture(zero=True, cleaning=0)
    orders = [replace(source.orders[0], count=3), source.orders[1]]
    return CalendarCycleInstance(source.network, orders, source.operating_policy,
                                 dict(batch_count_candidates=[1, 2]),
                                 source.cost_profile.config, model=8)


@pytest.mark.parametrize('kind', ['ordinary', 'late_release', 'multiple_satellites', 'unequal_counts'])
@pytest.mark.parametrize('formulation', ['expanded', 'compact'])
def test_optimum_and_two_cycle_replay_are_preserved(kind, formulation):
    problem = instance(kind)
    costs = []
    for profile in ('none', 'physical', 'physical_time'):
        result = solve_exact(problem, time_limit=20, strengthening=profile,
                             milp_options=dict(formulation=formulation))
        assert result['info']['proven_optimal'], result['info']
        audit = audit_solution(problem, result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert result['info']['objective'] == pytest.approx(audit['objective'])
        assert result['info']['complete_count_domain']
        assert result['info']['batch_count_candidates'] == list(problem.batch_count_candidates)
        assert result['info']['solver_config']['formulation_options']['strengthening'] == profile
        costs.append(audit['objective'])
    assert costs[0] == pytest.approx(costs[1])


def test_continuous_relaxation_obeys_indivisible_order_stock_requirement():
    problem = instance('unequal_counts')
    bounds = []
    for profile in ('none', 'physical'):
        model, _ = build_model(problem, batch_count=2, compact_e2=True, strengthening=profile)
        relaxed = None
        try:
            relaxed = model.relax()  # Drops indicators: deliberately optimistic lower bound.
            relaxed.Params.OutputFlag = 0
            relaxed.optimize()
            assert relaxed.Status == gp.GRB.OPTIMAL
            bounds.append(relaxed.ObjVal)
            if profile == 'physical':
                assert relaxed.getVarByName('container_pool').X >= 3 - 1e-7
        finally:
            if relaxed is not None:
                relaxed.dispose()
            model.dispose()
    assert bounds[1] >= bounds[0] - 1e-7


def test_infeasible_counts_remain_in_the_declared_domain():
    problem = calendar_fixture(8, counts=(3, 4))
    result = solve_exact(problem, time_limit=10)
    assert result['info']['proven_infeasible']
    assert result['info']['complete_count_domain']
    assert result['info']['batch_count_candidates'] == [3, 4]


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError, match='strengthening'):
        solve_exact(calendar_fixture(8), strengthening='heuristic')
