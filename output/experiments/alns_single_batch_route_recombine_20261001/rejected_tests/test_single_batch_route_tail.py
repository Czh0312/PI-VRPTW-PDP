"""Capacity-aware M2 suffix proposals remain subordinate to full audits."""
from copy import deepcopy
from dataclasses import replace
import random
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.assignment import assigned_problem, input_assignment
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Customer
from models.routing.service import solve_routing
from models.routing.solvers.alns.construction import reassigned_state
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.partitioned_assignment import (
    _route_tail_assignment, _tail_route_cost, solve_alns)
from tests.routing.test_single_batch_solver_audit import tiny_problem


class FirstOption:
    def choice(self, rows):
        return rows[0]


def tail_problem():
    base = tiny_problem(2, 1)
    nodes = dict(base.network.nodes)
    nodes['C3'] = Customer('C3',0,0,0,0,(0,100),(0,100),'T')
    distances = {a: {b: 7 if a == b else 20 for b in nodes} for a in nodes}
    for a, b, value in [('S','C0',1), ('C0','C1',20), ('C1','S',1),
                         ('T','C2',1), ('C2','C3',20), ('C3','T',1),
                         ('C0','C3',1), ('C3','S',1), ('C2','C1',1), ('C1','T',1)]:
        distances[a][b] = value
    travel = {a: {b: 0 for b in nodes} for a in nodes}
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=2, E1_Qk=100, E1_Ck=100,
                 E2_Qv=4, E2_Cv=2, container_weight=1, container_volume=1)
    orders = [replace(base.orders[0], id='o'+str(i), customer='C'+str(i),
                      satellite='S' if i < 2 else 'T', count=1, goods_weight=1)
              for i in range(4)]
    p = MultiBatchInstance(ProblemInstance(nodes, fleet, distance_matrix=distances,
                           travel_time_matrix=travel), orders, base.operating_policy, model=2)
    incumbent = dict(e2_trips=[dict(satellite_id=s, events=[dict(order_id=o, kind='d') for o in ids])
                     for s, ids in [('S',['o0','o1']), ('T',['o2','o3'])]])
    return p, incumbent


def test_full_directed_connector_gain_with_both_routes_already_full():
    p, incumbent = tail_problem()
    current = input_assignment(p)
    saved = deepcopy(incumbent), dict(current), p.orders
    candidate, delta = _route_tail_assignment(p, current, incumbent, FirstOption())
    assert candidate == dict(C0='S', C1='T', C2='T', C3='S')
    assert delta == -38
    # Access-only whole-route ranking cannot identify this internal-arc gain.
    assert p.distance('S','C0')+p.distance('C1','S') == 2
    assert p.distance('T','C2')+p.distance('C3','T') == 2
    assert _tail_route_cost(p,'S',(p.order_map['o0'],p.order_map['o3'])) == 3
    assert _tail_route_cost(p,'T',(p.order_map['o2'],p.order_map['o1'])) == 3
    assert saved == (incumbent,current,p.orders)


@pytest.mark.parametrize('quantity', ['goods_weight','container_weight','container_volume'])
def test_payload_screen_counts_goods_and_all_container_dimensions(quantity):
    p, incumbent = tail_problem()
    if quantity == 'goods_weight':
        orders = [replace(o,goods_weight=2) for o in p.orders]
        p = MultiBatchInstance(p.network,orders,p.operating_policy,model=2)
    elif quantity == 'container_weight':
        p.fleet.container_weight = 3
    else:
        p.fleet.container_volume = 2
    rng = random.Random(2);saved = rng.getstate()
    assert _tail_route_cost(p,'S',(p.order_map['o0'],p.order_map['o1'])) is None
    assert _route_tail_assignment(p,input_assignment(p),incumbent,rng) is None
    assert rng.getstate() == saved


def test_empty_route_has_zero_cost_with_nonzero_diagonal():
    p, _ = tail_problem()
    assert p.distance('S','S') == 7
    assert _tail_route_cost(p,'S',()) == 0


@pytest.mark.parametrize('boundary', ['window','cutoff','return'])
def test_optimistic_time_boundary_then_one_tick_violation(boundary):
    p, _ = tail_problem()
    order = p.order_map['o0']
    if boundary == 'window':
        order = replace(order,delivery_window=(10,10))
        p.travel = lambda a,b: 9 if a == 'S' and b == 'C0' else 0
    elif boundary == 'cutoff':
        cutoff = p.batch_map[order.batch].cutoff
        order = replace(order,delivery_window=(cutoff-order.service_time,cutoff))
        p.travel = lambda a,b: cutoff-order.service_time-p.operating_policy.e2_dispatch_prep_time if a == 'S' else 0
    else:
        policy = p.operating_policy
        current = max(policy.cycle_start+policy.e2_dispatch_prep_time,order.delivery_window[0])+order.service_time
        return_time = policy.cycle_end-current-policy.e2_return_unload_time-policy.e2_turnaround_time
        p.travel = lambda a,b: return_time if b == 'S' else 0
    assert _tail_route_cost(p,'S',(order,)) is not None
    old = p.travel
    p.travel = lambda a,b: old(a,b)+(1 if (b == 'S' if boundary == 'return' else a == 'S') else 0)
    assert _tail_route_cost(p,'S',(order,)) is None


@pytest.mark.parametrize('variant', ['no_trips','pickup','wrong_owner','same_site','zero','positive'])
def test_no_option_preserves_original_fallback_rng(variant):
    p, incumbent = tail_problem();current = input_assignment(p)
    if variant == 'no_trips':
        incumbent['e2_trips'] = []
    elif variant == 'pickup':
        incumbent['e2_trips'][0]['events'][0]['kind'] = 'p'
    elif variant == 'wrong_owner':
        current['C0'] = 'T'
    elif variant == 'same_site':
        current.update(C2='S',C3='S');incumbent['e2_trips'][1]['satellite_id'] = 'S'
    elif variant == 'zero':
        p.distance = lambda a,b: 0
    else:
        p.distance = lambda a,b: 1 if (a,b) in [('C0','C1'),('C2','C3')] else 20
    rng = random.Random(17);before = rng.getstate();saved = deepcopy((current,incumbent))
    assert _route_tail_assignment(p,current,incumbent,rng) is None
    assert rng.getstate() == before and (current,incumbent) == saved


def test_customer_with_order_outside_suffix_cannot_partially_change_owner():
    p, incumbent = tail_problem()
    # Other C1 order is outside the inspected trips; the C1 suffix is ineligible.
    duplicate = replace(p.order_map['o1'],id='o1_extra')
    p = MultiBatchInstance(p.network,[*p.orders,duplicate],p.operating_policy,model=2)
    rng = random.Random(6);before = rng.getstate()
    assert _route_tail_assignment(p,input_assignment(p),incumbent,rng) is None
    assert rng.getstate() == before


def test_all_orders_in_complete_customer_suffix_move_together():
    p, incumbent = tail_problem()
    p.fleet.e2_weight_cap = 6;p.fleet.e2_volume_cap = 3
    p = MultiBatchInstance(p.network,[*p.orders,replace(p.order_map['o1'],id='o1_extra')],
                           p.operating_policy,model=2)
    incumbent['e2_trips'][0]['events'].append(dict(order_id='o1_extra',kind='d'))
    candidate, delta = _route_tail_assignment(p,input_assignment(p),incumbent,FirstOption())
    assert delta < 0 and candidate['C1'] == 'T'
    fixed = assigned_problem(p,candidate)
    assert all(o.satellite == 'T' for o in fixed.orders if o.customer == 'C1')


def test_empty_suffix_allows_whole_route_merge():
    p, incumbent = tail_problem()
    p.fleet.e2_weight_cap = 8;p.fleet.e2_volume_cap = 4
    # One source is distant; complete transfer into the other route removes it.
    p.distance = lambda a,b: 20 if 'S' in (a,b) else 1
    candidate, delta = _route_tail_assignment(p,input_assignment(p),incumbent,FirstOption())
    assert delta < 0 and set(candidate.values()) == {'T'}


@pytest.mark.parametrize('seed', range(4))
def test_reproducible_complete_customer_proposal(seed):
    p, incumbent = tail_problem();current = input_assignment(p)
    a = _route_tail_assignment(p,current,incumbent,random.Random(seed))
    b = _route_tail_assignment(p,current,incumbent,random.Random(seed))
    assert a == b and set(a[0]) == set(current)


def test_actual_projection_is_complete_and_independently_feasible():
    p, _ = tail_problem();assignment = input_assignment(p);fixed = assigned_problem(p,assignment)
    state = dict(deliveries={'b1':[['S','T']]}, pickup_order={'b1':['S','T']},
                 pickup_vehicle={'b1':{}},e2=[dict(satellite=s,vehicle=0,tasks=[(oid,'d') for oid in ids])
                        for s,ids in [('S',['o0','o1']),('T',['o2','o3'])]])
    _, incumbent, issues = evaluate(fixed,state)
    assert not issues and audit_solution(fixed,incumbent)['valid']
    proposed, _ = _route_tail_assignment(p,assignment,incumbent,FirstOption())
    fixed = assigned_problem(p,proposed);before = deepcopy(incumbent)
    projected = reassigned_state(fixed,incumbent,time.perf_counter()+5)
    assert projected is not None and incumbent == before
    assert sorted(t for r in projected['e2'] for t in r['tasks']) == [(o.id,'d') for o in p.orders]
    assert all(fixed.order_map[oid].satellite == r['satellite'] for r in projected['e2'] for oid,kind in r['tasks'])
    _, result, issues = evaluate(fixed,projected)
    audit = audit_solution(fixed,result)
    assert not issues and audit['valid'] and audit['periodic_replay']['valid']


@pytest.mark.parametrize('model', [1,2,3,4])
def test_public_scope_and_independent_audit(monkeypatch, model):
    from models.routing.solvers.alns import partitioned_assignment
    original = partitioned_assignment._route_tail_assignment;calls = []
    def counted(*args):
        result = original(*args);calls.append(result is not None);return result
    monkeypatch.setattr(partitioned_assignment,'_route_tail_assignment',counted)
    p = tiny_problem(model,1)
    result = solve_routing(model=model,method='alns',problem=p,alns_time=10,alns_iter=160,seed=11)
    assert bool(calls) == (model == 2)
    if model == 2:
        assert result['info']['assignment_tail_checks'] == len(calls)
        assert result['info']['assignment_tail_attempts'] == sum(calls)
    audit = audit_solution(p,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert result['info']['best_bound'] is None and not result['info']['proven_optimal']


def test_disabled_option_and_zero_gain_fallback_reproduce_original_search(monkeypatch):
    from models.routing.solvers.alns import partitioned_assignment
    p = tiny_problem(2,0)
    a = solve_alns(p,max_time=10,max_iterations=160,seed=17)
    assert a['info']['assignment_tail_attempts'] == 0
    def forbidden(*args):
        raise AssertionError('Disabled option must retain the original proposal path')
    monkeypatch.setattr(partitioned_assignment,'_route_tail_assignment',forbidden)
    b = solve_alns(p,max_time=10,max_iterations=160,seed=17,route_recombination=False)
    for field in ('e1_trips','e2_trips','assignment'):
        assert a[field] == b[field]
    assert a['info']['objective'] == b['info']['objective']
    assert a['info']['iterations'] == b['info']['iterations'] == 160
    assert b['info']['assignment_tail_checks'] == 0
