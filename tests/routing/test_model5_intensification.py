"""Cycle descent must preserve semantics, budgets, and audited incumbents."""
from copy import deepcopy
from dataclasses import replace
import random
import time

import pytest

from models.common.cycle import MultiBatchInstance, Order
from models.common.cycle.audit import audit_solution
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Depot, Satellite, Customer
from models.routing.model_5.alns.evaluator import evaluate
from models.routing.model_5.alns.accelerators.intensification import CycleDescent, task_distance
from models.routing.model_5.alns.neighborhoods import state_key
from models.routing.model_5.alns.search.engine import solve_alns


def directed_cycle(seed=3):
    rng = random.Random(seed)
    nodes = {'D': Depot('D',0,0), 'S': Satellite('S',0,0)}
    for name in ('A','B','C'):
        nodes[name] = Customer(name,0,0,0,0,(0,125),(0,125),'S')
    distances = {a:{b:0 if a==b else rng.randint(1,25) for b in nodes} for a in nodes}
    durations = {a:{b:int(a!=b) for b in nodes} for a in nodes}
    policy = dict(policy_version='multi_batch_cutoff_v1',cycle_start=0,cycle_end=125,
        batches=[dict(id='b1',delivery_duration=30,return_duration=30),
                 dict(id='b2',delivery_duration=30,return_duration=30)],
        container_pool_size=3,cleaning_duration=5,time_tick_seconds=60,
        e2_max_trips_per_vehicle=4,e2_turnaround_time=1)
    fleet = dict(E1_vehicle_count=1,vehicles_per_satellite=2,E1_Qk=20,E1_Ck=20,
        E2_Qv=10,E2_Cv=10,container_weight=1,container_volume=1)
    orders = [Order(f'{b}_{c}',c,'S',b,1,1,(start,start+25),(start,start+28),0,2)
              for b,start in [('b1',0),('b2',60)] for c in ('A','B','C')]
    p = MultiBatchInstance(ProblemInstance(nodes,fleet,distance_matrix=distances,
                                          travel_time_matrix=durations),orders,policy,model=5)
    state = dict(deliveries={b:[['S']] for b in ('b1','b2')},
        pickup_order={b:['S'] for b in ('b1','b2')},
        pickup_vehicle={b:{} for b in ('b1','b2')},e2=[])
    for b in ('b1','b2'):
        state['e2'].append(dict(satellite='S',vehicle=0,
            tasks=[(f'{b}_{c}',k) for k in ('d','p') for c in ('A','B')]))
        state['e2'].append(dict(satellite='S',vehicle=1,tasks=[(f'{b}_C',k) for k in ('d','p')]))
    return p,state


@pytest.mark.parametrize('seed',[1,3,9])
def test_directed_descent_preserves_coverage_and_checks_the_whole_cycle(seed):
    p,state = directed_cycle(seed)
    original = deepcopy(state)
    _,decoded,issues = evaluate(p,state)
    assert not issues and audit_solution(p,decoded)['valid']
    descent = CycleDescent(p)
    expected = sorted(t for r in state['e2'] for t in r['tasks'])
    initial_distance = sum(task_distance(p,r['satellite'],r['tasks']) for r in state['e2'])
    found = 0
    while True:
        candidate = descent.propose(state,decoded,time.perf_counter()+2)
        if candidate is None:
            break
        assert sorted(t for r in candidate['e2'] for t in r['tasks']) == expected
        assert state == original
        if candidate['e2'] != state['e2']:
            assert sum(task_distance(p,r['satellite'],r['tasks']) for r in candidate['e2']) < initial_distance-1e-8
        score,result,problems = evaluate(p,candidate)
        if not problems:
            audit = audit_solution(p,result)
            assert audit['valid'] and audit['periodic_replay']['valid']
            assert score == pytest.approx(audit['objective'])
            found += 1
    assert found > 0
    assert descent.propose(state,decoded,0) is None


def test_fixed_iteration_reproducibility_and_best_solution_preservation():
    p,state = directed_cycle()
    _,warm,issues = evaluate(p,state)
    assert not issues
    saved = deepcopy(warm)
    results = [solve_alns(p,max_time=10,max_iterations=80,seed=42,initial_result=warm) for _ in range(2)]
    assert warm == saved
    assert results[0]['info']['objective'] == pytest.approx(results[1]['info']['objective'])
    assert results[0]['e2_trips'] == results[1]['e2_trips']
    for result in results:
        info = result['info']
        assert info['iterations'] == 80
        assert info['objective'] <= warm['info']['objective']
        assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
        assert not info['proven_optimal'] and not info['proven_infeasible']
        history = info['incumbent_history']
        assert all(a['objective'] > b['objective'] for a,b in zip(history,history[1:]))
        assert history[-1]['objective'] == info['objective']


def test_zero_iterations_does_not_search_and_tiny_budget_keeps_audited_seed():
    p,_ = directed_cycle()
    initial = solve_alns(p,max_time=10,max_iterations=0)
    assert initial['audit']['valid']
    assert initial['info']['iterations'] == 0
    assert initial['info']['descent_candidates'] == 0
    tiny = solve_alns(p,max_time=.001,max_iterations=100000,initial_result=initial)
    assert tiny['audit']['valid']
    assert tiny['info']['objective'] <= initial['info']['objective']
    assert tiny['info']['runtime'] < .5


def test_no_partial_mutation_when_candidate_is_changed_or_search_expires():
    p,state = directed_cycle()
    _,decoded,_ = evaluate(p,state)
    descent = CycleDescent(p)
    original = state_key(state)
    candidate = descent.propose(state,decoded,time.perf_counter()+1)
    assert candidate is not None
    candidate['e2'][0]['tasks'].clear()
    assert state_key(state) == original
    assert descent.propose(state,decoded,0) is None


def test_e1_swap_crosses_capacity_barrier_and_improves_audited_distance():
    base,_ = directed_cycle()
    nodes = {'D':Depot('D',0,0),'S':Satellite('S',10,0),'T':Satellite('T',-10,0),
        'A':Customer('A',11,0,0,0,(0,125),(0,125),'S'),
        'B':Customer('B',-11,0,0,0,(0,125),(0,125),'T')}
    fleet=dict(E1_vehicle_count=2,vehicles_per_satellite=1,E1_Qk=2,E1_Ck=1,
        E2_Qv=10,E2_Cv=10,container_weight=1,container_volume=1)
    orders=[Order(f'{b}_{c}',c,s,b,1,1,(t,t+25),(t,t+28),0,2)
        for b,t in [('b1',0),('b2',60)] for c,s in [('A','S'),('B','T')]]
    p=MultiBatchInstance(ProblemInstance(nodes,fleet),orders,
        replace(base.operating_policy,container_pool_size=4),model=5)
    state=dict(deliveries={b:[['S'],['T']] for b in ('b1','b2')},
        pickup_order={b:['S','T'] for b in ('b1','b2')},
        pickup_vehicle={b:{'S':1,'T':0} for b in ('b1','b2')},
        e2=[dict(satellite=o.satellite,vehicle=0,tasks=[(o.id,'d'),(o.id,'p')]) for o in orders])
    score,decoded,issues=evaluate(p,state)
    assert not issues and audit_solution(p,decoded)['valid']
    relocated=deepcopy(state)
    relocated['deliveries']['b1']=[[],['T','S']]
    assert 'E1_WEIGHT_CAPACITY' in evaluate(p,relocated)[2]
    descent=CycleDescent(p)
    found=False
    while True:
        candidate=descent.propose(state,decoded,time.perf_counter()+2)
        if candidate is None:break
        if candidate['deliveries']['b1']==[['T'],['S']]:
            new_score,result,issues=evaluate(p,candidate)
            assert not issues and new_score<score
            assert audit_solution(p,result)['periodic_replay']['valid']
            found=True
    assert found
