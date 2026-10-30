"""Full-cost chain proposals must retain coverage, clocks and audited incumbents."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import time

import pytest

from models.common.cycle.calendar import CalendarCycleInstance
from models.routing.model_8.alns.chain_repair import recovery_frontier, complete_request_repair, _profiles
from models.routing.model_8.alns.evaluator import JointEvaluator
from models.routing.model_8.alns.temporal_domains import proposal_view, temporal_assignment_bounds
from tests.routing.test_model6_joint import joint_fixture
from tests.routing.test_model8_evaluation_pool import sample


def stock_fixture(grid=None):
    source = joint_fixture(zero=True, cleaning=5, pool_rate=30)
    options = {'batch_count_candidates': [2]}
    if grid:
        options['calendar_decision_grid_seconds'] = grid
    p = CalendarCycleInstance(source.network, source.orders, source.operating_policy, options,
                              source.cost_profile.config, model=8).fixed_calendar(2)
    assignment = {'o1': 'b1', 'o2': 'b2'}
    cutoff = p.batch_map['b2'].start-p.operating_policy.cleaning_duration+1
    cutoff = -(-cutoff//p.calendar_decision_step)*p.calendar_decision_step
    state = dict(deliveries={b.id: [['S']] for b in p.operating_policy.batches},
        pickup_order={b.id: ['S'] for b in p.operating_policy.batches},
        pickup_vehicle={b.id: {'S': 0} for b in p.operating_policy.batches},
        cutoffs={**p.default_cutoffs, 'b1': cutoff}, e2=[
            dict(satellite='S', vehicle=0, tasks=[('o1', 'd'), ('o1', 'p')]),
            dict(satellite='S', vehicle=0, tasks=[('o2', 'd'), ('o2', 'p')])])
    return p, assignment, state, JointEvaluator(p)


@pytest.mark.parametrize('grid', [None, 120])
def test_joint_profiles_reduce_true_pool_cost_and_preserve_physical_grid(grid):
    p, assignment, state, ev = stock_fixture(grid)
    saved = deepcopy(state)
    before, result, _ = ev(assignment, state)
    assert result is not None and result['container_pool_size'] == 2
    proposed, calls, gains = recovery_frontier(p, ev, assignment, state, time.perf_counter()+10.)
    after, result, issues = ev(assignment, proposed)
    assert result is not None and not issues and result['audit']['periodic_replay']['valid']
    assert result['container_pool_size'] == 1 and after < before-29.9
    assert calls > 0 and gains > 0 and state == saved
    assert all(value % p.calendar_decision_step == 0 for value in proposed['cutoffs'].values())
    assert not result['info']['proven_optimal']


def test_identical_return_times_are_not_split_between_productive_batches():
    p, _, _, _ = stock_fixture()
    raw = dict(e1_trips=[], e2_trips=[dict(return_complete=10, events=[{'kind': 'p'}]),
                                     dict(return_complete=10, events=[{'kind': 'p'}])])
    assert _profiles(p, raw, p.default_cutoffs) == []


def test_expired_chain_budget_retains_a_complete_incumbent():
    p, assignment, state, ev = stock_fixture()
    saved = deepcopy(state)
    proposed, calls, gains = recovery_frontier(p, ev, assignment, state, time.perf_counter()-1.)
    assert proposed == state == saved and calls == gains == 0
    proposed, calls, gains = complete_request_repair(p, ev, assignment, state, ['o1'], time.perf_counter()-1.)
    assert proposed == state == saved and calls == gains == 0


def test_chain_profile_does_not_depend_on_cutoff_dict_insertion_order():
    p, assignment, state, ev = stock_fixture()
    reversed_state = deepcopy(state)
    reversed_state['cutoffs'] = dict(reversed(list(state['cutoffs'].items())))
    a, _, _ = recovery_frontier(p, ev, assignment, state, time.perf_counter()+10.)
    b, _, _ = recovery_frontier(p, ev, assignment, reversed_state, time.perf_counter()+10.)
    assert ev(assignment, a)[0] == ev(assignment, b)[0]


def test_complete_request_moves_score_complete_plans_and_reduce_real_cost(monkeypatch):
    source = joint_fixture(zero=True, one_batch=True)
    source.fleet.e2_per_sat_count = 2
    p = CalendarCycleInstance(source.network, source.orders, source.operating_policy,
        {'batch_count_candidates': [1]}, source.cost_profile.config, model=8).fixed_calendar(1)
    assignment = {o.id: 'b1' for o in p.orders}
    state = dict(deliveries={'b1': [['S']]}, pickup_order={'b1': ['S']}, pickup_vehicle={'b1': {'S': 0}},
        cutoffs=dict(p.default_cutoffs), e2=[dict(satellite='S', vehicle=i, tasks=[(o.id, 'd'), (o.id, 'p')])
                                           for i, o in enumerate(p.orders)])
    expected = Counter(task for row in state['e2'] for task in row['tasks'])
    original = JointEvaluator.__call__
    def evaluate(self, chosen, trial):
        assert Counter(task for row in trial['e2'] for task in row['tasks']) == expected
        return original(self, chosen, trial)
    monkeypatch.setattr(JointEvaluator, '__call__', evaluate)
    ev = JointEvaluator(p)
    before, result, _ = ev(assignment, state)
    assert result is not None
    saved = deepcopy(state)
    proposed, calls, gains = complete_request_repair(p, ev, assignment, state, ['o1', 'o2'], time.perf_counter()+10.)
    after, result, _ = ev(assignment, proposed)
    assert result is not None and result['audit']['periodic_replay']['valid'] and after < before
    assert calls > 0 and gains > 0 and state == saved


def test_proposal_time_domains_force_early_order_without_mutating_input():
    source = joint_fixture(zero=True)
    orders = [replace(source.orders[0], delivery_window=(0, 10), pickup_window=(70, 80)),
              replace(source.orders[1], delivery_window=(0, 110), pickup_window=(100, 110))]
    variable = CalendarCycleInstance(source.network, orders, source.operating_policy,
        {'batch_count_candidates': [2]}, source.cost_profile.config, model=8).for_count(2)
    saved = deepcopy(variable.legal_batches)
    assert saved['o1'] == ('b1', 'b2')
    view, bounds = proposal_view(variable)
    assert view.legal_batches['o1'] == ('b1',) and bounds['certificate'] is None
    assert variable.legal_batches == saved


def test_productive_matching_certificate_retains_full_input_K_domain():
    source = joint_fixture(zero=True)
    parent = CalendarCycleInstance(source.network, source.orders, source.operating_policy,
        {'batch_count_candidates': [1, 2, 3]}, source.cost_profile.config, model=8)
    variable = parent.for_count(3)
    bounds = temporal_assignment_bounds(variable)
    assert bounds['certificate'] is not None
    assert list(parent.batch_count_candidates) == [1, 2, 3]
    assert variable.legal_batches == {o.id: ('b1', 'b2', 'b3') for o in variable.orders}


def test_necessary_domains_do_not_remove_an_independently_audited_assignment():
    p, assignment, state = sample()
    ev = JointEvaluator(p)
    assert ev(assignment, state)[1]['audit']['periodic_replay']['valid']
    view, bounds = proposal_view(p)
    assert bounds['certificate'] is None
    assert all(assignment[oid] in view.legal_batches[oid] for oid in assignment)


def test_three_productive_batches_keep_a_feasible_reuse_chain_on_refined_clock():
    source = joint_fixture(zero=True)
    orders = [replace(source.orders[0], delivery_window=(0, 20), pickup_window=(20, 30)),
              replace(source.orders[1], delivery_window=(45, 65), pickup_window=(65, 70)),
              replace(source.orders[1], id='o3', delivery_window=(85, 105), pickup_window=(105, 110))]
    parent = CalendarCycleInstance(source.network, orders, source.operating_policy,
        {'batch_count_candidates': [3]}, source.cost_profile.config, model=8)
    p = parent.fixed_calendar(3)
    assignment = {o.id: f'b{index+1}' for index, o in enumerate(p.orders)}
    state = dict(deliveries={b.id: [['S']] for b in p.operating_policy.batches},
        pickup_order={b.id: ['S'] for b in p.operating_policy.batches},
        pickup_vehicle={b.id: {'S': 0} for b in p.operating_policy.batches},
        cutoffs=dict(p.default_cutoffs), e2=[dict(satellite='S', vehicle=0, tasks=[(o.id, 'd'), (o.id, 'p')])
                                           for o in p.orders])
    result = JointEvaluator(p)(assignment, state)[1]
    assert result is not None and result['audit']['periodic_replay']['valid']
    assert result['container_pool_size'] == 1
    view, bounds = proposal_view(parent.for_count(3))
    assert bounds['certificate'] is None
    assert all(assignment[oid] in view.legal_batches[oid] for oid in assignment)
