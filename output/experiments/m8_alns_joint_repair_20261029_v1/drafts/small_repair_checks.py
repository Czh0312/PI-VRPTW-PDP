"""Physical invariants for bounded construction and retained-request repairs."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import random
import time

import pytest

from models.common.cycle.calendar import CalendarCycleInstance
from models.common.cycle.instance import Order
from models.routing.model_8.alns.batch_initialization import temporal_assignments
from models.routing.model_8.alns.construction import initial_state, _best_construction
from models.routing.model_8.alns.evaluator import JointEvaluator
from models.routing.model_8.alns.neighborhoods import _goods_ready
from models.routing.model_8.alns.route_beam import beam_routes, _simulate
from models.routing.model_8.alns.small_repair import retained_repair, polish_small, _e1_and_wait_moves
from models.routing.model_8.alns.state import state_key
from tests.routing.test_model6_joint import joint_fixture


def parent_fixture(counts=(2,), orders=3, one_batch=False):
    source = joint_fixture(zero=True, one_batch=one_batch)
    source.fleet.e2_per_sat_count = 2
    rows = list(source.orders)
    for index in range(len(rows), orders):
        rows.append(replace(rows[0], id=f'extra{index}'))
    return CalendarCycleInstance(source.network, rows, source.operating_policy,
        {'batch_count_candidates': list(counts)}, source.cost_profile.config, model=8)


def state_fixture(count=2):
    parent = parent_fixture((count,), orders=max(3, count))
    p = parent.fixed_calendar(count)
    assignment = {o.id: f'b{index%count+1}' for index, o in enumerate(p.orders)}
    ev = JointEvaluator(p)
    value, state, result, issues = _best_construction(p, ev, assignment, random.Random(42), time.perf_counter()+10.)
    assert result is not None and not issues
    return p, assignment, state, ev


def tasks(state):
    return Counter(task for route in state['e2'] for task in route['tasks'])


def test_retained_batch_repair_preserves_requests_and_unchanged_task_order():
    p, assignment, state, ev = state_fixture()
    moved = p.orders[2].id
    proposed = {**assignment, moved: 'b2'}
    saved = deepcopy(state)
    value = retained_repair(p, ev, assignment, proposed, state, state['cutoffs'], time.perf_counter()+10.)
    assert value is not None and value[3] is not None
    assert value[3]['audit']['valid'] and value[3]['audit']['periodic_replay']['valid']
    assert tasks(value[2]) == tasks(state)
    before = [task for route in state['e2'] for task in route['tasks'] if task[0] != moved]
    after = [task for route in value[2]['e2'] for task in route['tasks'] if task[0] != moved]
    assert before == after
    assert state == saved and assignment[moved] == 'b1'
    assert not value[3]['info']['proven_optimal']


def test_interrupted_retained_repair_does_not_expose_a_partial_plan():
    p, assignment, state, ev = state_fixture()
    proposed = {**assignment, p.orders[2].id: 'b2'}
    saved = deepcopy(state)
    assert retained_repair(p, ev, assignment, proposed, state, state['cutoffs'], time.perf_counter()-1.) is None
    assert state == saved


def test_retained_repair_keeps_feasible_waits_when_earliest_timing_fails(monkeypatch):
    p, assignment, state, ev = state_fixture()
    state['e2'][0]['return_after'] = 3
    original = JointEvaluator.__call__
    assert original(ev, assignment, state)[1] is not None
    def evaluate(self, chosen, trial):
        if not any('return_after' in route for route in trial['e2']):
            return self.penalty, None, ('E1_VEHICLE_OVERLAP',)
        return original(self, chosen, trial)
    monkeypatch.setattr(JointEvaluator, '__call__', evaluate)
    value = retained_repair(p, ev, assignment, assignment, state, state['cutoffs'], time.perf_counter()+10.)
    assert value[3] is not None and value[3]['audit']['periodic_replay']['valid']
    assert value[2]['e2'][0]['return_after'] == 3


def test_recovery_move_keeps_earlier_batches_and_is_independent_of_dict_order():
    p, assignment, state, ev = state_fixture(count=3)
    fixed = ev.fixed(assignment, state['cutoffs'])
    state['e2'] = [dict(satellite='S', vehicle=0, tasks=[(o.id, 'd'), (o.id, 'p')]) for o in p.orders]
    physical = {'e1_trips': [], 'e2_trips': [
        dict(return_complete=state['cutoffs'][f'b{index+1}']-1, events=[{'kind': 'p'}])
        for index in range(3)]}
    first = list(_e1_and_wait_moves(p, assignment, state, physical, time.perf_counter()+10.))
    target = state['cutoffs']['b2']+1
    assert any(trial['e2'][1].get('return_after') == target and
               'return_after' not in trial['e2'][0] for trial in first)
    shuffled = deepcopy(state)
    shuffled['cutoffs'] = dict(reversed(list(shuffled['cutoffs'].items())))
    second = list(_e1_and_wait_moves(p, assignment, shuffled, physical, time.perf_counter()+10.))
    def signature(trial):
        return state_key(trial), tuple(sorted(trial['cutoffs'].items()))
    assert {signature(trial) for trial in first} == {signature(trial) for trial in second}


def test_small_polish_preserves_incumbent_on_deadline_and_never_accepts_bad_cost():
    p, assignment, state, ev = state_fixture()
    score, _, _ = ev(assignment, state)
    saved = deepcopy(state)
    stopped, evaluations, improvements = polish_small(p, ev, assignment, state, time.perf_counter()-1.)
    assert stopped == saved and evaluations == improvements == 0
    polished, _, _ = polish_small(p, ev, assignment, state, time.perf_counter()+10.)
    value, result, _ = ev(assignment, polished)
    assert result is not None and result['audit']['periodic_replay']['valid']
    assert value <= score+1e-8 and tasks(polished) == tasks(state) and state == saved


def beam_fixture():
    parent = parent_fixture((1,), orders=2, one_batch=True)
    p = parent.fixed_calendar(1)
    assignment = {o.id: 'b1' for o in p.orders}
    ev = JointEvaluator(p)
    state = initial_state(ev.fixed(assignment), random.Random(42))
    state['cutoffs'] = dict(p.default_cutoffs)
    state['e2'] = [dict(satellite='S', vehicle=index, tasks=[(o.id, 'd'), (o.id, 'p')])
                   for index, o in enumerate(p.orders)]
    assert ev(assignment, state)[1] is not None
    return p, assignment, state, ev


def test_beam_builds_cheaper_fully_audited_routes_with_exact_task_coverage():
    p, assignment, state, ev = beam_fixture()
    saved = deepcopy(state)
    before, _, _ = ev(assignment, state)
    proposed = beam_routes(p, ev, assignment, state, time.perf_counter()+10.)
    after, result, _ = ev(assignment, proposed)
    assert result is not None and result['audit']['periodic_replay']['valid']
    assert after < before and tasks(proposed) == tasks(state) and state == saved
    assert not result['info']['proven_optimal']


def test_beam_timeout_retains_the_original_complete_incumbent():
    p, assignment, state, ev = beam_fixture()
    assert beam_routes(p, ev, assignment, state, time.perf_counter()-1.) == state


def test_local_trip_simulation_rejects_precedence_and_mixed_delivery_batches():
    p, assignment, state, ev = state_fixture()
    fixed = ev.fixed(assignment)
    fixed._joint_cost_rates = p.cost_profile.rates
    goods = _goods_ready(fixed, state)
    assert _simulate(fixed, 'S', [(p.orders[0].id, 'p')], goods, 0, {}) is None
    assert _simulate(fixed, 'S', [(p.orders[0].id, 'd'), (p.orders[1].id, 'd')], goods, 0, {}) is None


def test_temporal_seeds_include_window_clusters_and_preserve_domains():
    parent = parent_fixture((2,), orders=2)
    p = replace(parent.operating_policy, cycle_end=120, container_pool_size=7,
                e2_max_trips_per_vehicle=10,
                batches=tuple(replace(b, start=60*i, cutoff=60*i+40, end=60*(i+1))
                              for i, b in enumerate(parent.operating_policy.batches)))
    orders = [Order(f't{i}', 'C1', 'S', 'b1', 1, 1,
                    (0, 10) if i < 5 else (50, 60) if i == 5 else (90, 100),
                    (10, 20) if i < 5 else (60, 70) if i == 5 else (100, 110),
                    0, 0, 0, ('b1', 'b2')) for i in range(7)]
    variable = CalendarCycleInstance(parent.network, orders, p, {'batch_count_candidates': [2]},
                                    parent.cost_profile.config, model=8).for_count(2)
    variable.legal_batches['t0'] = ('b1',)
    before = deepcopy(variable.legal_batches)
    assignments = temporal_assignments(variable, limit=8)
    assert assignments and Counter(assignments[0].values()) == {'b1': 5, 'b2': 2}
    assert all(a['t0'] == 'b1' and set(a) == set(variable.order_map) for a in assignments)
    assert variable.legal_batches == before and temporal_assignments(variable, limit=0) == []
