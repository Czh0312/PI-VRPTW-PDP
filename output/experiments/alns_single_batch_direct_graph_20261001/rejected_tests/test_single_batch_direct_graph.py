"""Direct integer timing graph preserves complete decoder results and violation order."""
from copy import deepcopy
from dataclasses import replace
import random

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.alns.evaluator import evaluate, initial_state
from models.routing.solvers.alns.single_batch import _move
from models.routing.solvers.alns.single_batch_cache import SingleBatchCache, SingleBatchEvaluator
from tests.routing.test_single_batch_solver_audit import tiny_problem


@pytest.mark.parametrize('model', [1, 3])
@pytest.mark.parametrize('seed', range(6))
def test_cached_loads_match_uncached_complete_decoder(model, seed):
    p = tiny_problem(model, seed)
    rng = random.Random(31)
    state = initial_state(p, rng)
    cached = SingleBatchEvaluator(p, load_capacity=2, dense_timing=True)
    uncached = SingleBatchEvaluator(p, load_capacity=0)
    for step in range(100):
        expected = evaluate(p, state)
        assert cached(p, state) == uncached(p, state) == expected
        assert cached.load_summary.cache_info().currsize <= 2
        state = _move(p, state, rng,
            ('relocate', 'reverse', 'vehicle', 'split_merge', 'e1_delivery', 'e1_pickup', 'pair')[step % 7])
        if state['e2'] and step % 10 == 0:
            state['e2'][0].update(depart_after=step, return_after=step+8)
    assert cached.load_summary.cache_info().hits > 0
    assert uncached.load_summary is None


def test_aggregate_preserves_floating_addition_order_and_distinct_task_kinds():
    base = tiny_problem(3, 1)
    orders = [replace(o, goods_weight=1e16 if i == 0 else 0)
              for i, o in enumerate(base.orders)]
    p = MultiBatchInstance(base.network, orders, base.operating_policy, model=3)
    memo = SingleBatchEvaluator(p, load_capacity=2)
    forward = tuple((o.id, 'd') for o in orders)
    backward = forward[::-1]
    for sequence in (forward, backward, forward+forward[:1], tuple((o.id, 'p') for o in orders)):
        batches, weight, volume = memo.load_summary(sequence)
        assert batches == {p.order_map[o].batch for o, k in sequence if k == 'd'}
        assert weight == sum(p.task(o, k)[1] for o, k in sequence if k == 'd')
        assert volume == sum(p.task(o, k)[2] for o, k in sequence if k == 'd')
        assert memo.load_summary.cache_info().currsize <= 2
        with pytest.raises(AttributeError):
            batches.add('corrupt')
    assert memo.load_summary(forward)[1] != memo.load_summary(backward)[1]


def _outcome(call):
    try:
        return call()
    except (KeyError, ValueError) as error:
        return type(error), str(error)


@pytest.mark.parametrize('case', ['unknown_delivery', 'unknown_pickup', 'invalid_kind',
                                 'duplicate_before_unknown', 'wrong_site', 'missing_task'])
def test_invalid_routes_keep_reference_error_and_coverage_priority(case):
    p = tiny_problem(3, 1)
    state = initial_state(p, random.Random(3))
    route = state['e2'][0]
    if case == 'unknown_delivery':
        route['tasks'].append(('unknown', 'd'))
    elif case == 'unknown_pickup':
        route['tasks'].append(('unknown', 'p'))
    elif case == 'invalid_kind':
        route['tasks'].append((p.orders[0].id, 'invalid'))
    elif case == 'duplicate_before_unknown':
        route['tasks'][:] = [route['tasks'][0], route['tasks'][0], ('unknown', 'p')]
    elif case == 'wrong_site':
        route['satellite'] = next(s for s in p.satellites if s != route['satellite'])
    else:
        route['tasks'].pop()
    cached = SingleBatchEvaluator(p, dense_timing=True)
    expected = _outcome(lambda: evaluate(p, state))
    assert _outcome(lambda: cached(p, state)) == expected
    assert _outcome(lambda: cached(p, deepcopy(state))) == expected


def test_same_tasks_recompute_waits_goods_event_loads_and_fresh_result_objects():
    p = tiny_problem(3, 1)
    state = initial_state(p, random.Random(2))
    memo = SingleBatchEvaluator(p, dense_timing=True)
    baseline = memo(p, state)
    saved = deepcopy(baseline)
    baseline[1]['e2_trips'][0]['events'][0]['start'] = 999
    assert memo(p, state) == saved
    warm_hits = memo.load_summary.cache_info().hits
    for variant in range(4):
        changed = deepcopy(state)
        if variant == 0:
            changed['e2'][0]['depart_after'] = 90
        elif variant == 1:
            changed['e2'][0]['return_after'] = 90
        elif variant == 2:
            changed['deliveries']['b1'][0].reverse()
        else:
            changed['e2'][0]['tasks'].reverse()
        expected = evaluate(p, changed)
        actual = memo(p, changed)
        assert actual == expected
        assert audit_solution(p, actual[1]) == audit_solution(p, expected[1])
        assert saved == evaluate(p, state)
    assert memo.load_summary.cache_info().hits > warm_hits


def test_new_context_reprepares_loads_after_input_change_and_capacity_reaches_cache():
    p = tiny_problem(3, 1)
    tasks = tuple((o.id, 'd') for o in p.orders)
    first = SingleBatchEvaluator(p)
    before = first.load_summary(tasks)
    p.fleet.container_weight += 3
    second = SingleBatchEvaluator(p)
    assert second.load_summary(tasks)[1] == sum(p.task(o, k)[1] for o, k in tasks)
    assert second.load_summary(tasks) != before
    assert first.load_summary(tasks) == before
    assert SingleBatchCache(p, load_capacity=0).evaluator.load_summary is None
    assert SingleBatchCache(p, load_capacity=3).evaluator.load_summary.cache_info().maxsize == 3

@pytest.mark.parametrize('seed', range(24))
def test_direct_graph_matches_old_node_order_even_for_cycles_and_parallel_edges(seed):
    from models.routing.solvers.alns.single_batch_timing import propagate_dense, propagate_times
    rng = random.Random(seed)
    nodes = list(range(seed % 13));rng.shuffle(nodes)
    bounds = {('n', i): rng.random()*100 for i in nodes}
    edges = [(('n', a), ('n', b), rng.random()*30)
             for a in nodes for b in nodes if rng.random() < .17]
    rng.shuffle(edges)
    if edges:edges.append(edges[0])
    keys = list(bounds);indices = {node:i for i,node in enumerate(keys)}
    values = list(bounds.values())
    dense_edges = [(indices[a], indices[b], delta) for a,b,delta in edges]
    saved = list(dense_edges)
    a = propagate_times(bounds, edges);b = propagate_dense(values, dense_edges)
    assert a == b and list(bounds.values()) == values and dense_edges == saved


@pytest.mark.parametrize('variant', ['emptyroute', 'deliverypickupcycle', 'duplicate', 'interleavedvehicles', 'waits'])
def test_direct_graph_handles_route_index_gaps_and_crossroute_constraints(variant):
    p = tiny_problem(3, 1)
    state = initial_state(p, random.Random(1))
    if variant == 'emptyroute':
        state['e2'].insert(0, dict(satellite='S', vehicle=0, tasks=[]))
    elif variant == 'deliverypickupcycle':
        route = state['e2'][0]
        oid = route['tasks'][0][0]
        route['tasks'] = [(oid, 'p')]+[t for t in route['tasks'] if t != (oid, 'p')]
    elif variant == 'duplicate':
        state['e2'][0]['tasks'].append(state['e2'][0]['tasks'][0])
    elif variant == 'interleavedvehicles':
        rows = []
        for route in state['e2']:
            rows.extend([{**route, 'tasks': [t]} for t in route['tasks']])
        state['e2'] = rows[::2]+rows[1::2]
    else:
        for i, route in enumerate(state['e2']):
            route.update(depart_after=30+i, return_after=70+i)
    memo = SingleBatchEvaluator(p, dense_timing=True)
    expected = evaluate(p, state)
    assert memo(p, state) == expected == SingleBatchEvaluator(p)(p, state)
    assert audit_solution(p, memo(p, state)[1]) == audit_solution(p, expected[1])


@pytest.mark.parametrize('model', [1, 2, 3, 4])
def test_public_direct_timing_activation_only_m3(monkeypatch, model):
    from models.routing.service import solve_routing
    original = SingleBatchEvaluator.__init__
    enabled = []
    def init(self, *args, **kwargs):
        enabled.append(kwargs.get('dense_timing', False))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(SingleBatchEvaluator, '__init__', init)
    result = solve_routing(model=model, method='alns', problem=tiny_problem(model, 1),
                           alns_time=5, alns_iter=100, seed=11)
    assert enabled and all(flag == (model == 3) for flag in enabled)
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']


def test_direct_timing_default_off_and_context_identity_guard():
    p = tiny_problem(3, 1)
    memo = SingleBatchEvaluator(p, dense_timing=True)
    assert not SingleBatchEvaluator(p).dense_timing and not SingleBatchCache(p).evaluator.dense_timing
    assert SingleBatchCache(p, dense_timing=True).evaluator.dense_timing
    with pytest.raises(ValueError, match='one problem'):
        memo(tiny_problem(3, 0), initial_state(p, random.Random(1)))
