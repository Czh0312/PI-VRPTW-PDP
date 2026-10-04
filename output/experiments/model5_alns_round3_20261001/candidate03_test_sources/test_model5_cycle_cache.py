"""Cached E1 schedules must agree with full decode, including invalid states."""
from copy import deepcopy
from dataclasses import replace
import random
import time

import pytest

from models.routing.solvers.alns.cycle_cache import CycleEvaluator
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.intensification import CycleDescent
from models.routing.solvers.alns.neighborhoods import clone_state
from models.routing.solvers.alns.solver import _move
from tests.routing.test_model5_intensification import directed_cycle
from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Satellite, Customer


def test_cycle_cache_matches_full_decoder_across_moves_and_cutoffs():
    problem, original = directed_cycle()
    cached = CycleEvaluator(problem, capacity=8)
    rng = random.Random(1701)
    state = original
    operators = ['relocate','pair','reverse','vehicle','split_merge',
                 'cutoff','e1_delivery','e1_pickup']
    valid = invalid = 0
    for step in range(160):
        if step % 8 == 0:
            state = clone_state(original)
        state = _move(problem,state,rng,operators[step % len(operators)])
        expected = evaluate(problem,state)
        assert cached(problem,state) == expected
        valid += not expected[2]
        invalid += bool(expected[2])
    assert valid and invalid and cached.hits and cached.misses
    assert len(cached.entries) <= 8


def test_return_cache_includes_quantities_hints_order_and_every_batch():
    problem, state = directed_cycle()
    cached = CycleEvaluator(problem)
    quantities = {b.id:{'S':3} for b in problem.operating_policy.batches}
    first = cached.returns(state,quantities)
    expected = deepcopy(first)
    first[0][0]['events'].clear()
    assert cached.returns(state,quantities) == expected
    assert cached.hits == 1
    # A changed late-batch return must not reuse an earlier clean-stock release.
    changed = deepcopy(quantities)
    changed['b2']['S'] = 2
    cached.returns(state,changed)
    state['pickup_vehicle']['b2']['S'] = 0
    cached.returns(state,changed)
    state['pickup_order']['b2'] = []
    cached.returns(state,changed)
    state['deliveries']['b2'] = [[]]
    cached.returns(state,changed)
    assert cached.misses == 5
    with pytest.raises(ValueError):
        cached(directed_cycle()[0],state)


def test_site_distance_cache_ignores_waits_but_rechecks_timing():
    problem, state = directed_cycle()
    descent = CycleDescent(problem)
    deadline = time.perf_counter()+10
    original = descent._e2_candidates(state,deadline)
    assert original
    enumerations = descent.enumerations
    changed = clone_state(state)
    for route in changed['e2']:
        route['return_after'] = problem.operating_policy.cycle_end
    assert descent._e2_candidates(changed,deadline) == original
    assert descent.enumerations == enumerations
    assert descent.site_cache_hits == 1
    _, decoded, _ = evaluate(problem,state)
    # Every cached E2 candidate now fails the local timing filter; E1 proposals
    # remain possible, but no stale E2 time-feasibility decision is reused.
    while True:
        candidate = descent.propose(changed,decoded,deadline)
        if candidate is None:
            break
        assert candidate['e2'] == changed['e2']
    assert descent.filtered > 0


def test_site_cache_remaps_route_indices_after_other_satellite_moves():
    base, state = directed_cycle()
    nodes = dict(base.network.nodes)
    nodes['T'] = Satellite('T',0,0)
    for name in ('A','B','C'):
        nodes['T'+name] = Customer('T'+name,0,0,0,0,(0,125),(0,125),'T')
    distances = {a:{b:0 if a==b else (len(a)*7+len(b)*3+ord(b[-1]))%19+1
                   for b in nodes} for a in nodes}
    orders = list(base.orders)+[replace(o,id='T'+o.id,customer='T'+o.customer,satellite='T') for o in base.orders]
    fleet = dict(E1_vehicle_count=1,vehicles_per_satellite=2,E1_Qk=40,E1_Ck=40,
                 E2_Qv=10,E2_Cv=10,container_weight=1,container_volume=1)
    problem = MultiBatchInstance(ProblemInstance(nodes,fleet,distance_matrix=distances),
        orders,replace(base.operating_policy,container_pool_size=6),model=5)
    extra = [{**r,'satellite':'T','tasks':[('T'+o,k) for o,k in r['tasks']]} for r in state['e2']]
    state['e2'] = extra+state['e2']
    descent = CycleDescent(problem,capacity=2048)
    deadline = time.perf_counter()+10
    before = descent._e2_candidates(state,deadline)
    enumerations = descent.enumerations
    changed = clone_state(state)
    changed['e2'] = changed['e2'][len(extra):]+changed['e2'][:len(extra)]
    after = descent._e2_candidates(changed,deadline)
    def identified(candidates, source):
        return {tuple((source['e2'][i]['satellite'],tuple(source['e2'][i]['tasks']),tuple(tasks))
                      for i,tasks in changes) for _,changes in candidates}
    assert before and identified(before,state) == identified(after,changed)
    assert descent.enumerations == enumerations
    assert descent.site_cache_hits == 2
