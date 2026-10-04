"""Per-site geometry must keep the full enumerator's global tie order."""
from copy import deepcopy
from dataclasses import replace
import random
import time
from types import SimpleNamespace

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.single_batch_descent import SingleBatchDescent
from tests.routing.test_single_batch_descent_readiness import sequential_trips
from tests.routing.test_single_batch_solver_audit import tiny_problem


def interleaved(seed):
    base = tiny_problem(3, seed)
    orders = [replace(o, id=f'{o.id}_{i}') for i in range(4) for o in base.orders]
    p = MultiBatchInstance(base.network, orders,
        replace(base.operating_policy, e2_max_trips_per_vehicle=12), model=3)
    by_site = {s: [o for o in orders if o.satellite == s] for s in p.satellites}
    groups = {s: [row[:1], row[1:]] for s, row in by_site.items()}
    routes = [dict(satellite=s, vehicle=0,
                   tasks=[(o.id, 'd') for o in groups[s][i]]+
                         [(o.id, 'p') for o in reversed(groups[s][i])])
              for i in range(2) for s in p.satellites]
    return p, dict(deliveries={'b1': [list(p.satellites)]},
        pickup_order={'b1': list(p.satellites)}, pickup_vehicle={'b1': {}}, e2=routes)


def engines(p, capacity=256):
    return (SingleBatchDescent(p, capacity=capacity, cache_sites=False, cache_distances=True),
            SingleBatchDescent(p, capacity=capacity, cache_sites=True, cache_distances=True,
                               preserve_global_order=True))


@pytest.mark.parametrize('capacity', [1, 3, 16, 256])
@pytest.mark.parametrize('seed', range(6))
def test_complete_geometry_and_ties_match_uncached_global_enumeration(seed, capacity):
    p, state = interleaved(seed)
    original = deepcopy(state)
    reference, cached = engines(p, capacity)
    expected = reference._e2_candidates(state, time.perf_counter()+20)
    assert cached._e2_candidates(state, time.perf_counter()+20) == expected
    calls = cached.enumerations
    assert cached._e2_candidates(state, time.perf_counter()+20) == expected
    assert cached.enumerations == calls and cached.site_cache_hits == len(p.satellites)
    assert state == original


@pytest.mark.parametrize('change', ['reverse', 'remove', 'reorder', 'waits', 'vehicle', 'e1'])
def test_changes_in_one_site_remap_global_indices_and_keep_other_site_cached(change):
    p, state = interleaved(1)
    reference, cached = engines(p, 16)
    cached._e2_candidates(state, time.perf_counter()+20)
    before = cached.enumerations
    changed = deepcopy(state)
    if change == 'reverse':
        changed['e2'][0]['tasks'].reverse()
    elif change == 'remove':
        changed['e2'].pop(0)
    elif change == 'reorder':
        changed['e2'] = [changed['e2'][i] for i in (1, 0, 3, 2)]
    elif change == 'waits':
        changed['e2'][0].update(depart_after=23, return_after=70)
    elif change == 'vehicle':
        changed['e2'][0]['vehicle'] = 1
    else:
        changed['deliveries']['b1'][0].reverse()
        changed['pickup_order']['b1'].reverse()
        changed['pickup_vehicle']['b1']['S'] = 0
    assert cached._e2_candidates(changed, time.perf_counter()+20) == reference._e2_candidates(
        changed, time.perf_counter()+20)
    assert cached.site_cache_hits > 0
    if change in ('reverse', 'remove'):
        assert cached.enumerations == before+1
    elif change in ('waits', 'vehicle', 'e1'):
        assert cached.enumerations == before


def test_complete_proposal_sequence_and_deferred_recovery_remain_identical():
    p, state, _ = sequential_trips(3)
    _, decoded, issues = evaluate(p, state)
    assert not issues
    original = deepcopy(state)
    reference, cached = engines(p)
    improved = False
    while True:
        a = reference.propose(state, decoded, time.perf_counter()+10)
        b = cached.propose(state, decoded, time.perf_counter()+10)
        assert a == b
        if a is None:
            break
        score, result, violations = evaluate(p, b)
        if not violations:
            audit = audit_solution(p, result)
            assert audit['valid'] and audit['periodic_replay']['valid']
            improved |= score < decoded['info']['objective']
        b['e2'][0]['tasks'].clear()
        assert state == original
    assert improved and reference.recovered == cached.recovered


def test_global_tie_order_differs_from_legacy_group_order_and_is_restored():
    from models.routing.common.domain.instance import ProblemInstance
    p, state = interleaved(1)
    nodes = p.network.nodes
    matrix = {a: {b: 0 if a == b else 1 for b in nodes} for a in nodes}
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1, E1_Qk=100, E1_Ck=100,
                 E2_Qv=100, E2_Cv=100, container_weight=1, container_volume=1)
    p = MultiBatchInstance(ProblemInstance(nodes, fleet, distance_matrix=matrix,
        travel_time_matrix=matrix), p.orders, p.operating_policy, model=3)
    for route in state['e2']:
        oid = route['tasks'][0][0]
        route['tasks'] = [(oid, 'd'), (oid, 'p')]
    state['e2'][3]['tasks'] = [('o0_1', 'd'), ('o0_1', 'p')]
    reference, cached = engines(p)
    legacy = SingleBatchDescent(p, cache_sites=True, cache_distances=True)
    expected = reference._e2_candidates(state, time.perf_counter()+2)
    assert legacy._e2_candidates(state, time.perf_counter()+2) != expected
    assert cached._e2_candidates(state, time.perf_counter()+2) == expected


def test_partial_deadline_enumeration_is_not_saved(monkeypatch):
    from models.routing.solvers.alns import single_batch_descent as module
    p, state = interleaved(1)
    state['e2'] = [r for r in state['e2'] if r['satellite'] == 'S']
    cached = engines(p)[1]
    clock, expire, calls = [0], [True], []
    monkeypatch.setattr(module, 'time', SimpleNamespace(perf_counter=lambda: clock[0]))
    def enumerate_routes(routes, deadline):
        calls.append(True)
        if expire[0]:
            clock[0] = 2
        return []
    monkeypatch.setattr(cached, '_enumerate_routes', enumerate_routes)
    assert cached._e2_candidates(state, 1) == []
    assert not cached.site_options
    clock[0], expire[0] = 0, False
    cached._e2_candidates(state, 1)
    assert len(calls) == 2 and len(cached.site_options) == 1
    cached._e2_candidates(state, 1)
    assert len(calls) == 2 and cached.site_cache_hits == 1


def test_geometry_eviction_and_contexts_are_bounded_and_independent(monkeypatch):
    p, state = interleaved(1)
    state['e2'] = [r for r in state['e2'] if r['satellite'] == 'S']
    cached = engines(p)[1]
    monkeypatch.setattr(cached, '_enumerate_routes', lambda routes, deadline: [])
    original = list(state['e2'][0]['tasks'])
    for count in range(1, 70):
        state['e2'][0]['tasks'] = original*count
        cached._e2_candidates(state, time.perf_counter()+1)
    assert len(cached.site_options) == 64
    fresh = engines(p)[1]
    assert not fresh.site_options and fresh.site_cache_hits == 0
