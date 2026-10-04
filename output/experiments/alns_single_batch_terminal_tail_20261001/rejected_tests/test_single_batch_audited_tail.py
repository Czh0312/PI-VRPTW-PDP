"""An M2 suffix seed is retained only after complete feasible improvement."""
from copy import deepcopy
from dataclasses import replace
import random
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.assignment import assigned_problem, input_assignment, lift_result
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Customer
from models.routing.service import solve_routing
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.neighborhoods import clone_state, state_from_result
from models.routing.solvers.alns.partitioned_assignment import (
    _audited_tail_seed, _tail_options, _tail_state, _tail_route_cost, solve_alns)
from tests.routing.test_single_batch_solver_audit import tiny_problem


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


def audited_incumbent(p):
    state = dict(deliveries={'b1':[['S','T']]},pickup_order={'b1':['S','T']},pickup_vehicle={'b1':{}},
                 e2=[dict(satellite=s,vehicle=0,tasks=[(oid,'d') for oid in ids])
                     for s,ids in [('S',['o0','o1']),('T',['o2','o3'])]])
    extras = [o for o in p.orders if o.id not in ('o0','o1','o2','o3')]
    state['e2'].extend(dict(satellite=o.satellite,vehicle=1,tasks=[(o.id,'d')]) for o in extras)
    assignment = input_assignment(p);fixed = assigned_problem(p,assignment)
    _,result,issues = evaluate(fixed,state)
    assert not issues and audit_solution(fixed,result)['valid']
    return lift_result(p,result,assignment)


def test_complete_audited_seed_preserves_directed_tail_order_and_incumbent():
    p,_ = tail_problem();incumbent = audited_incumbent(p);current = incumbent['assignment']
    saved = deepcopy((incumbent,current));rng = random.getstate();statistics = {}
    seed = _audited_tail_seed(p,current,incumbent,time.perf_counter()+5,statistics)
    assert seed['assignment'] == dict(C0='S',C1='T',C2='T',C3='S')
    assert seed['geometry_delta'] == -38 and seed['gain'] == 38
    assert seed['result']['info']['objective'] == 66 and seed['mode'] == 'direct'
    assert seed['context'].problem is seed['fixed']
    audit = audit_solution(seed['fixed'],seed['result'])
    assert audit['valid'] and audit['periodic_replay']['valid']
    lifted = lift_result(p,seed['result'],seed['assignment'])
    assert lifted['audit']['valid'] and not lifted['info']['proven_optimal']
    assert (incumbent,current) == saved and random.getstate() == rng
    assert statistics['decoded_candidates'] == 2 and statistics['improving_candidates'] == 1


def test_independent_audit_failure_never_supplies_warm_seed(monkeypatch):
    from models.routing.solvers.alns import partitioned_assignment as module
    p,_ = tail_problem();incumbent = audited_incumbent(p);stats = {};calls = []
    def rejected(*args):
        calls.append(1);return dict(valid=False)
    monkeypatch.setattr(module,'audit_solution',rejected)
    assert _audited_tail_seed(p,incumbent['assignment'],incumbent,time.perf_counter()+5,stats) is None
    assert calls and stats['audited_candidates'] == len(calls) and stats['improving_candidates'] == 0


def test_global_supply_delay_rejects_locally_feasible_geometric_exchange():
    p,_ = tail_problem()
    p = MultiBatchInstance(p.network,[replace(o,delivery_window=(0,10)) if o.id == 'o1' else o for o in p.orders],
                           p.operating_policy,model=2)
    p._travel['S','T'] = 20
    incumbent = audited_incumbent(p);current = incumbent['assignment'];original = state_from_result(p,incumbent)
    options = _tail_options(p,current,original,time.perf_counter()+5)
    assert len(options) == 1 and options[0][0] == -38
    assignment,fixed,state = _tail_state(p,current,original,options[0])
    score,result,issues = evaluate(fixed,state)
    assert 'E2_TIME_WINDOW' in issues and not audit_solution(fixed,result)['valid']
    stats = {}
    assert _audited_tail_seed(p,current,incumbent,time.perf_counter()+5,stats) is None
    assert stats['decoded_candidates'] == 2 and stats['audited_candidates'] == 0


def test_repaired_complete_seed_can_win_over_preserved_order(monkeypatch):
    from models.routing.solvers.alns import partitioned_assignment as module
    p,_ = tail_problem()
    old_distance = p.distance
    p.distance = lambda a,b: 0 if (a,b) in [('S','C3'),('C3','C0'),('C0','S')] else old_distance(a,b)
    incumbent = audited_incumbent(p);current = incumbent['assignment'];original = state_from_result(p,incumbent)
    option = _tail_options(p,current,original,time.perf_counter()+5)[0]
    _,fixed,repaired = _tail_state(p,current,original,option)
    repaired['e2'][0]['tasks'].reverse()
    assert audit_solution(fixed,evaluate(fixed,repaired)[1])['valid']
    calls = []
    def projection(*args,**kwargs):
        calls.append(1);return clone_state(repaired)
    monkeypatch.setattr(module,'reassigned_state',projection)
    stats = {};seed = _audited_tail_seed(p,current,incumbent,time.perf_counter()+5,stats)
    assert calls and seed['mode'] == 'repaired' and seed['gain'] == 41
    assert seed['result']['info']['objective'] == 63
    assert stats['improving_candidates'] == 2


def test_valid_direct_seed_survives_missing_projection(monkeypatch):
    from models.routing.solvers.alns import partitioned_assignment as module
    p,_ = tail_problem();incumbent = audited_incumbent(p);stats = {}
    monkeypatch.setattr(module,'reassigned_state',lambda *args,**kwargs:None)
    seed = _audited_tail_seed(p,incumbent['assignment'],incumbent,time.perf_counter()+5,stats)
    assert seed['mode'] == 'direct' and seed['result']['audit']['valid']
    assert stats['decoded_candidates'] == 1


def test_at_most_three_geometric_options_and_six_full_decodes(monkeypatch):
    from models.routing.solvers.alns import partitioned_assignment as module
    p,_ = tail_problem();incumbent = audited_incumbent(p);current = incumbent['assignment']
    options = _tail_options(p,current,state_from_result(p,incumbent),time.perf_counter()+5)
    monkeypatch.setattr(module,'_tail_options',lambda *args:options*10)
    stats = {};seed = _audited_tail_seed(p,current,incumbent,time.perf_counter()+5,stats)
    assert seed and stats['geometric_options'] == 10 and stats['decoded_candidates'] == 6


@pytest.mark.parametrize('variant',['expired','scan_timeout','zero_geometry','wrong_owner','same_site'])
def test_no_seed_fallback_is_immutable_and_deterministic(monkeypatch,variant):
    from models.routing.solvers.alns import partitioned_assignment as module
    p,_ = tail_problem();incumbent = audited_incumbent(p);current = dict(incumbent['assignment'])
    deadline = time.perf_counter()+5
    if variant == 'expired':
        deadline = 0
    elif variant == 'scan_timeout':
        monkeypatch.setattr(module,'_tail_options',lambda *args:None)
    elif variant == 'zero_geometry':
        p.distance = lambda a,b:0
    elif variant == 'wrong_owner':
        current['C0'] = 'T'
    else:
        current.update(C2='S',C3='S')
        for trip in incumbent['e2_trips']:
            trip['satellite_id'] = 'S'
    saved = deepcopy((incumbent,current));rng = random.getstate();stats = {}
    assert _audited_tail_seed(p,current,incumbent,deadline,stats) is None
    assert stats['decoded_candidates'] == 0
    assert stats['timed_out'] == int(variant in ('expired','scan_timeout'))
    assert (incumbent,current) == saved and random.getstate() == rng


def test_partial_customer_order_suffix_is_ineligible_with_full_incumbent():
    p,_ = tail_problem()
    p = MultiBatchInstance(p.network,[*p.orders,replace(p.order_map['o1'],id='o1_extra')],p.operating_policy,model=2)
    incumbent = audited_incumbent(p);stats = {}
    assert _audited_tail_seed(p,incumbent['assignment'],incumbent,time.perf_counter()+5,stats) is None
    assert stats['geometric_options'] == 0


def test_complete_multiorder_customer_suffix_is_preserved_and_audited():
    p,_ = tail_problem();p.fleet.e2_weight_cap = 6;p.fleet.e2_volume_cap = 3
    p = MultiBatchInstance(p.network,[*p.orders,replace(p.order_map['o1'],id='o1_extra')],p.operating_policy,model=2)
    current = input_assignment(p);fixed = assigned_problem(p,current)
    state = state_from_result(p,audited_incumbent(p));state['e2'] = [r for r in state['e2'] if ('o1_extra','d') not in r['tasks']]
    state['e2'][0]['tasks'].append(('o1_extra','d'))
    _,result,issues = evaluate(fixed,state);assert not issues and audit_solution(fixed,result)['valid']
    incumbent = lift_result(p,result,current);stats = {}
    seed = _audited_tail_seed(p,current,incumbent,time.perf_counter()+5,stats)
    assert seed and seed['assignment']['C1'] == 'T'
    assert all(o.satellite == 'T' for o in seed['fixed'].orders if o.customer == 'C1')
    tasks = sorted((e['order_id'],e['kind']) for r in seed['result']['e2_trips'] for e in r['events'])
    assert tasks == sorted((o.id,'d') for o in p.orders) and seed['result']['audit']['valid']


def test_whole_route_merge_removes_inactive_e1_visit_and_is_fully_audited():
    p,_ = tail_problem();p.fleet.e2_weight_cap = 8;p.fleet.e2_volume_cap = 4
    p.distance = lambda a,b:20 if 'S' in (a,b) else 1
    incumbent = audited_incumbent(p);stats = {}
    seed = _audited_tail_seed(p,incumbent['assignment'],incumbent,time.perf_counter()+5,stats)
    assert seed and set(seed['assignment'].values()) == {'T'}
    assert all(e['site_id'] != 'S' for r in seed['result']['e1_trips'] for e in r['events'])
    assert seed['gain'] > -seed['geometry_delta']
    lifted = lift_result(p,seed['result'],seed['assignment'])
    assert lifted['audit']['valid'] and lifted['audit']['periodic_replay']['valid']


def test_audited_seed_is_retained_by_zero_iteration_fixed_subsolve():
    from models.routing.solvers.alns.single_batch import solve_alns as fixed_solve
    p,_ = tail_problem();incumbent = audited_incumbent(p);stats = {}
    seed = _audited_tail_seed(p,incumbent['assignment'],incumbent,time.perf_counter()+5,stats)
    result = fixed_solve(seed['fixed'],max_time=10,max_iterations=0,seed=11,
                         initial_result=seed['result'],_context=seed['context'])
    assert result['audit']['valid'] and result['info']['objective'] <= seed['result']['info']['objective']


@pytest.mark.parametrize('model',[1,2,3,4])
def test_public_scope_and_audit(monkeypatch,model):
    from models.routing.solvers.alns import partitioned_assignment as module
    original = module._audited_tail_seed;calls = []
    def counted(*args):
        result = original(*args);calls.append(result);return result
    monkeypatch.setattr(module,'_audited_tail_seed',counted)
    p = tiny_problem(model,1)
    result = solve_routing(model=model,method='alns',problem=p,alns_time=10,alns_iter=160,seed=11)
    assert bool(calls) == (model == 2)
    if model == 2:
        assert result['info']['assignment_terminal_checks'] == len(calls)
        assert result['info']['assignment_terminal_steps'] == sum(r is not None for r in calls)
        assert result['info']['assignment_budget_policy'] == 'initial_40pct_then_project_and_refine'
    audit = audit_solution(p,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert result['info']['best_bound'] is None and not result['info']['proven_optimal']


def test_disabled_and_zero_geometry_fallback_use_original_search(monkeypatch):
    from models.routing.solvers.alns import partitioned_assignment as module
    p = tiny_problem(2,0)
    a = solve_alns(p,max_time=10,max_iterations=160,seed=17)
    def forbidden(*args):
        raise AssertionError('Disabled route seeds must use original proposals')
    monkeypatch.setattr(module,'_audited_tail_seed',forbidden)
    b = solve_alns(p,max_time=10,max_iterations=160,seed=17,terminal_route_descent=False)
    for field in ('e1_trips','e2_trips','assignment'):
        assert a[field] == b[field]
    assert a['info']['objective'] == b['info']['objective']
    assert a['info']['iterations'] == b['info']['iterations'] == 160
    assert a['info']['assignment_terminal_steps'] == b['info']['assignment_terminal_checks'] == 0


@pytest.mark.parametrize('quantity',['goods_weight','container_weight','container_volume'])
def test_payload_geometry_screen_keeps_full_container_dimensions(quantity):
    p,_ = tail_problem()
    if quantity == 'goods_weight':
        orders = [replace(o,goods_weight=2) for o in p.orders]
        p = MultiBatchInstance(p.network,orders,p.operating_policy,model=2)
    elif quantity == 'container_weight':
        p.fleet.container_weight = 3
    else:
        p.fleet.container_volume = 2
    assert _tail_route_cost(p,'S',(p.order_map['o0'],p.order_map['o1'])) is None


@pytest.mark.parametrize('boundary',['window','cutoff','return'])
def test_local_time_boundary_and_one_tick_violation(boundary):
    p,_ = tail_problem();order = p.order_map['o0']
    if boundary == 'window':
        order = replace(order,delivery_window=(10,10))
        p.travel = lambda a,b:9 if a == 'S' and b == 'C0' else 0
    elif boundary == 'cutoff':
        cutoff = p.batch_map[order.batch].cutoff
        order = replace(order,delivery_window=(cutoff-order.service_time,cutoff))
        p.travel = lambda a,b:cutoff-order.service_time-p.operating_policy.e2_dispatch_prep_time if a == 'S' else 0
    else:
        policy = p.operating_policy
        current = max(policy.cycle_start+policy.e2_dispatch_prep_time,order.delivery_window[0])+order.service_time
        return_time = policy.cycle_end-current-policy.e2_return_unload_time-policy.e2_turnaround_time
        p.travel = lambda a,b:return_time if b == 'S' else 0
    assert _tail_route_cost(p,'S',(order,)) is not None
    old = p.travel
    p.travel = lambda a,b:old(a,b)+(1 if (b == 'S' if boundary == 'return' else a == 'S') else 0)
    assert _tail_route_cost(p,'S',(order,)) is None


def test_empty_sequence_does_not_charge_nonzero_matrix_diagonal():
    p,_ = tail_problem()
    assert p.distance('S','S') == 7 and _tail_route_cost(p,'S',()) == 0


def test_terminal_acceptance_updates_final_best_and_history_without_extra_alns_iteration(monkeypatch):
    from models.routing.solvers.alns import single_batch
    p,_ = tail_problem();incumbent = audited_incumbent(p)
    fixed_result = deepcopy(incumbent);fixed_result['info']['iterations'] = 1
    monkeypatch.setattr(single_batch,'solve_alns',lambda *args,**kwargs:deepcopy(fixed_result))
    before = solve_alns(p,max_time=10,max_iterations=1,seed=11,terminal_route_descent=False)
    after = solve_alns(p,max_time=10,max_iterations=1,seed=11)
    assert before['info']['objective'] == 104 and after['info']['objective'] == 66
    assert after['info']['iterations'] == 1
    assert after['info']['assignment_terminal_steps'] == 1
    assert after['info']['assignment_terminal_gains'] == [38]
    assert after['info']['history_scope'] == 'completed_partition_subsolves_and_terminal_descent'
    assert [r['objective'] for r in after['info']['incumbent_history']] == [104,66]
    audit = audit_solution(p,after)
    assert audit['valid'] and audit['periodic_replay']['valid']


def test_terminal_does_not_reaccept_equal_audited_result(monkeypatch):
    from models.routing.solvers.alns import single_batch,partitioned_assignment as module
    p,_ = tail_problem();incumbent = audited_incumbent(p);stats = {}
    seed = _audited_tail_seed(p,incumbent['assignment'],incumbent,time.perf_counter()+5,stats)
    fixed_result = deepcopy(incumbent);fixed_result['info']['iterations'] = 1
    monkeypatch.setattr(single_batch,'solve_alns',lambda *args,**kwargs:deepcopy(fixed_result))
    calls = []
    def unchanged(*args):
        calls.append(1);args[-1].update({key:0 for key in stats});return seed
    monkeypatch.setattr(module,'_audited_tail_seed',unchanged)
    result = solve_alns(p,max_time=10,max_iterations=1)
    assert len(calls) == 2 and result['info']['assignment_terminal_steps'] == 1
    assert result['info']['assignment_terminal_gains'] == [38]


def test_zero_iteration_budget_skips_terminal_neighborhood(monkeypatch):
    from models.routing.solvers.alns import partitioned_assignment as module
    def forbidden(*args):
        raise AssertionError('Construction-only solve must not run terminal descent')
    monkeypatch.setattr(module,'_audited_tail_seed',forbidden)
    p = tiny_problem(2,1)
    result = solve_alns(p,max_time=10,max_iterations=0)
    assert result['info']['iterations'] == 0 and result['info']['assignment_terminal_checks'] == 0
    assert result['audit']['valid']


def test_expired_global_deadline_skips_terminal_work(monkeypatch):
    from models.routing.solvers.alns import single_batch,partitioned_assignment as module
    p,_ = tail_problem();incumbent = audited_incumbent(p);clock = [0]
    fixed_result = deepcopy(incumbent);fixed_result['info']['iterations'] = 1
    def solver(*args,**kwargs):
        clock[0] = 2;return deepcopy(fixed_result)
    def forbidden(*args):
        raise AssertionError('Expired global budget must not start terminal work')
    monkeypatch.setattr(single_batch,'solve_alns',solver)
    monkeypatch.setattr(module.time,'perf_counter',lambda:clock[0])
    monkeypatch.setattr(module,'_audited_tail_seed',forbidden)
    result = solve_alns(p,max_time=1,max_iterations=1)
    assert result['info']['assignment_terminal_checks'] == 0 and result['info']['objective'] == 104
