"""Unexecuted prototype contracts; retained with the rejected search variants."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import random
import time

import pytest

from models.common.cycle.calendar import CalendarCycleInstance
from models.common.cycle.instance import Order
from models.routing.model_8.alns.batch_initialization import temporal_assignments
from models.routing.model_8.alns.calendar_polish import deadline_calendar_seeds
from models.routing.model_8.alns.construction import _best_construction
from models.routing.model_8.alns.evaluator import JointEvaluator
from models.routing.model_8.alns.polish import _merge_trip_neighbors, _return_wait_neighbors
from tests.routing.test_model6_joint import joint_fixture


def temporal_fixture():
    base = joint_fixture(zero=True)
    policy = replace(base.operating_policy, cycle_end=120, container_pool_size=7,
        e2_max_trips_per_vehicle=10,
        batches=tuple(replace(b, start=60*i, cutoff=60*i+40, end=60*(i+1))
                      for i, b in enumerate(base.operating_policy.batches)))
    orders = []
    for index in range(7):
        delivery = (0, 10) if index < 5 else (50, 60) if index == 5 else (90, 100)
        pickup = (10, 20) if index < 5 else (60, 70) if index == 5 else (100, 110)
        orders.append(Order(f't{index}', 'C1', 'S', 'b1', 1, 1, delivery, pickup,
                            0, 0, 0, ('b1', 'b2')))
    return CalendarCycleInstance(base.network, orders, policy, {'batch_count_candidates': [2]},
                                 base.cost_profile.config, model=8)


def test_window_clusters_propose_useful_unbalanced_seed_from_inputs():
    parent = temporal_fixture()
    variable = parent.for_count(2)
    assignments = temporal_assignments(variable)
    assert assignments and len(assignments) <= 8
    first = assignments[0]
    assert Counter(first.values()) == {'b1': 5, 'b2': 2}
    assert all(first[f't{i}'] == ('b1' if i < 5 else 'b2') for i in range(7))
    assert list(parent.batch_count_candidates) == [2]


def test_temporal_seed_is_fully_decoded_and_audited():
    parent = temporal_fixture()
    bounds, assignment, cutoffs = next(deadline_calendar_seeds(parent.for_count(2)))
    child = parent.fixed_calendar(2, bounds)
    cutoffs = child.proposed_cutoffs({bid: min(hi, max(lo, cutoffs[bid]))
                                    for bid, (lo, hi) in child.cutoff_bounds.items()})
    evaluator = JointEvaluator(child)
    score, state, result, issues = _best_construction(child, evaluator, assignment,
        random.Random(42), time.perf_counter()+2., cutoffs=cutoffs)
    assert not issues and result is not None
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
    assert result['container_pool_size'] == 5
    assert score == result['audit']['objective']


def test_seed_generation_is_bounded_and_preserves_order_batch_domains():
    parent = temporal_fixture()
    variable = parent.for_count(2)
    original = deepcopy(variable.legal_batches)
    variable.legal_batches['t0'] = ('b1',)
    assignments = temporal_assignments(variable, limit=2)
    assert len(assignments) <= 2
    assert all(a['t0'] == 'b1' and set(a) == set(variable.order_map) for a in assignments)
    assert variable.legal_batches == {**original, 't0': ('b1',)}
    assert temporal_assignments(variable, limit=0) == []


def merge_fixture():
    base = joint_fixture(zero=True, one_batch=True)
    base.fleet.e2_per_sat_count = 2
    parent = CalendarCycleInstance(base.network, base.orders, base.operating_policy,
        {'batch_count_candidates': [1]}, base.cost_profile.config, model=8)
    p = parent.fixed_calendar(1)
    assignment = {o.id: 'b1' for o in p.orders}
    state = dict(deliveries={'b1': [['S']]}, pickup_order={'b1': ['S']},
        pickup_vehicle={'b1': {'S': 0}}, cutoffs=dict(p.default_cutoffs),
        e2=[dict(satellite='S', vehicle=i, tasks=[(o.id, 'd'), (o.id, 'p')])
            for i, o in enumerate(p.orders)])
    return p, assignment, state


def test_merging_cross_vehicle_trips_can_improve_without_single_task_intermediate():
    p, assignment, state = merge_fixture()
    evaluator = JointEvaluator(p)
    score, result, issues = evaluator(assignment, state)
    assert result is not None and not issues
    saved = deepcopy(state)
    fixed = evaluator.fixed(assignment, state['cutoffs'])
    values = [evaluator(assignment, trial) for trial in _merge_trip_neighbors(fixed, state, time.perf_counter()+1.)]
    feasible = [value for value in values if value[1] is not None]
    assert feasible and min(value[0] for value in feasible) < score
    assert state == saved
    assert all(value[1]['audit']['periodic_replay']['valid'] for value in feasible)


def test_merge_preserves_every_task_and_respects_deadline():
    p, assignment, state = merge_fixture()
    fixed = JointEvaluator(p).fixed(assignment, state['cutoffs'])
    expected = Counter(task for route in state['e2'] for task in route['tasks'])
    proposals = list(_merge_trip_neighbors(fixed, state, time.perf_counter()+1.))
    assert proposals
    for trial in proposals:
        assert Counter(task for route in trial['e2'] for task in route['tasks']) == expected
        assert len(trial['e2']) == len(state['e2'])-1
    assert not list(_merge_trip_neighbors(fixed, state, time.perf_counter()-1.))


def test_different_delivery_batches_are_not_merged():
    p = temporal_fixture().fixed_calendar(2)
    assignment = {o.id: 'b1' if i < 5 else 'b2' for i, o in enumerate(p.orders)}
    fixed = JointEvaluator(p).fixed(assignment)
    state = dict(e2=[dict(satellite='S', vehicle=0, tasks=[('t0', 'd'), ('t0', 'p')]),
                     dict(satellite='S', vehicle=1, tasks=[('t6', 'd'), ('t6', 'p')])])
    assert not list(_merge_trip_neighbors(fixed, state, time.perf_counter()+1.))


def test_group_return_wait_moves_together_after_cutoff_and_does_not_mutate_input():
    p = temporal_fixture().fixed_calendar(2)
    assignment = {o.id: 'b1' if i < 5 else 'b2' for i, o in enumerate(p.orders)}
    fixed = JointEvaluator(p).fixed(assignment)
    state = dict(deliveries={b.id: [['S']] for b in p.operating_policy.batches},
        pickup_order={b.id: ['S'] for b in p.operating_policy.batches},
        pickup_vehicle={b.id: {'S': 0} for b in p.operating_policy.batches},
        cutoffs=dict(p.default_cutoffs), e2=[
            dict(satellite='S', vehicle=0, tasks=[('t0', 'd'), ('t0', 'p')]),
            dict(satellite='S', vehicle=0, tasks=[('t1', 'd'), ('t1', 'p')])])
    saved = deepcopy(state)
    proposals = list(_return_wait_neighbors(fixed, state, time.perf_counter()+1.))
    target = state['cutoffs']['b1']+1
    assert any(all(route.get('return_after') == target for route in trial['e2']) for trial in proposals)
    assert state == saved
    assert not list(_return_wait_neighbors(fixed, state, time.perf_counter()-1.))
