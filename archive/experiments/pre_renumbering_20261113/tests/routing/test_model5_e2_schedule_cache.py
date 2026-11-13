"""E2 event reuse must preserve timing boundaries and all downstream costs."""
from copy import deepcopy
import random

import pytest

from models.common.cycle.audit import audit_solution
from models.common.cycle.fixed_pool import FixedBatchPoolInstance
from models.routing.model_5.alns.evaluator import _e2_schedule, evaluate, initial_state
from models.routing.model_5.alns.performance.cycle_cache import CycleEvaluator
from models.routing.model_5.alns.performance.e2_schedule_cache import E2ScheduleCache
from models.routing.model_5.alns.search import _move
from models.routing.model_5.alns.solver import solve_alns
from tests.routing.test_model5_intensification import directed_cycle
from tests.routing.test_multibatch_cycle import fixture
from tools.configuration.loader import load_json


def pool_problem():
    base = fixture(pool=2, cleaning=5)
    return FixedBatchPoolInstance(base.network, base.orders, base.operating_policy, {},
        load_json('config/experiments/benchmark27_v1/economic.json'))


def test_cache_matches_full_decoder_for_feasible_and_invalid_moves():
    problem, original = directed_cycle()
    cached = CycleEvaluator(problem, cache_e2_schedule=True)
    cached.e2_cache.capacity = 8
    rng = random.Random(1701)
    state = original
    operators = ['relocate', 'pair', 'reverse', 'vehicle', 'split_merge',
                 'cutoff', 'e1_delivery', 'e1_pickup']
    valid = invalid = 0
    for step in range(240):
        if step % 8 == 0:
            state = deepcopy(original)
        state = _move(problem, state, rng, operators[step % len(operators)])
        expected = evaluate(problem, state)
        assert cached(problem, state) == expected
        assert cached(problem, state) == expected
        valid += not expected[2]
        invalid += bool(expected[2])
    assert valid and invalid and cached.e2_cache.hits and cached.e2_cache.misses
    assert len(cached.e2_cache.entries) <= 8
    with pytest.raises(ValueError, match='one problem'):
        cached(directed_cycle()[0], state)


def test_supply_times_and_both_waits_invalidate_cached_events():
    problem, state = directed_cycle()
    cached = E2ScheduleCache(problem)
    goods = {('S', b.id): b.start + 1 for b in problem.operating_policy.batches}
    first = cached.decode(state, goods)
    saved = deepcopy(first)
    first[0][0]['events'].clear()
    first[2].append('CALLER_MUTATION')
    assert cached.decode(state, goods) == saved
    assert cached.hits == 1
    late_goods = {**goods, ('S', 'b1'): 15}
    late = cached.decode(state, late_goods)
    assert late == _e2_schedule(problem, state, late_goods)
    assert late[0][0]['departure'] > saved[0][0]['departure']
    for field in ('depart_after', 'return_after'):
        changed = deepcopy(state)
        changed['e2'][0][field] = 29
        assert cached.decode(changed, goods) == _e2_schedule(problem, changed, goods)
    assert cached.misses == 4


def test_cache_preserves_cutoff_equality_and_rejects_empty_recovery():
    problem = pool_problem()
    original = initial_state(problem, random.Random(42))
    cached = CycleEvaluator(problem, cache_e2_schedule=True)
    outputs = []
    for delay in (0, 1):
        state = deepcopy(original)
        state['e2'][0]['return_after'] = problem.operating_policy.batches[0].cutoff + delay
        actual = cached(problem, state)
        assert actual == evaluate(problem, state)
        if not delay:
            assert not actual[2]
            assert audit_solution(problem, actual[1])['periodic_replay']['valid']
        else:
            assert actual[2] == ['EMPTY_BATCH_RECOVERY']
        outputs.append(actual)
    assert outputs[0][1]['container_pool_size'] == 1
    assert outputs[1][0] > outputs[0][0] + 1e5
    assert cached.e2_cache.misses == 2


def test_e1_reverse_changes_reuse_events_but_recheck_downstream_schedule():
    problem, state = directed_cycle()
    cached = CycleEvaluator(problem, cache_e2_schedule=True)
    first = cached(problem, state)
    changed = deepcopy(state)
    changed['pickup_order']['b1'] = []
    actual = cached(problem, changed)
    assert actual == evaluate(problem, changed)
    assert actual[1]['e2_trips'] == first[1]['e2_trips']
    assert actual[1]['e1_trips'] != first[1]['e1_trips']
    assert 'CLEAN_STOCK_SHORTAGE' in actual[2]
    assert cached.e2_cache.hits == 1


@pytest.mark.parametrize('mutation,code', [
    ('vehicle', 'INVALID_E2_VEHICLE'), ('duplicate', 'E2_TASK_COVERAGE'),
    ('cycle', 'TIMING_CYCLE'),
])
def test_hard_failures_survive_cache_hits(mutation, code):
    problem, state = directed_cycle()
    if mutation == 'vehicle':
        state['e2'][0]['vehicle'] = 999
    elif mutation == 'duplicate':
        state['e2'][0]['tasks'].append(state['e2'][0]['tasks'][0])
    else:
        # Pickup before its own delivery creates a customer precedence cycle.
        state['e2'][0]['tasks'].reverse()
    cached = CycleEvaluator(problem, cache_e2_schedule=True)
    expected = evaluate(problem, state)
    assert expected[2] == [code]
    assert cached(problem, state) == expected
    assert cached(problem, state) == expected
    assert cached.e2_cache.hits == 1


def test_public_switch_and_fixed_iteration_search_preserve_audited_result():
    problem = pool_problem()
    results = [solve_alns(problem, max_time=10, max_iterations=80, seed=42,
                          cache_e2_schedule=flag) for flag in (False, True)]
    assert results[0]['info']['objective'] == results[1]['info']['objective']
    assert results[0]['e1_trips'] == results[1]['e1_trips']
    assert results[0]['e2_trips'] == results[1]['e2_trips']
    for flag, result in zip((False, True), results):
        assert result['info']['e2_schedule_cache_enabled'] is flag
        assert result['info']['iterations'] == 80
        assert audit_solution(problem, result)['periodic_replay']['valid']
    assert results[1]['info']['e2_schedule_cache_hits'] > 0
