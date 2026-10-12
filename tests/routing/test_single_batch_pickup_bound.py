"""Independent mixed-task route/LP oracles and original schedule projection."""
from collections import Counter
from copy import copy
from dataclasses import replace
import math
import random
import time

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.audit import audit_solution
from models.routing.model_4.exact.single_batch_pickup_bound import pickup_contexts, e1_component_floor, project_original_schedule, root_pickup_route_bound
from models.routing.model_4.exact.single_batch_columns import problem_fingerprint
from models.routing.model_4.exact.pickup_route_pricing import RoutePricer
from models.routing.model_4.exact.engine import solve_exact
from tests.routing.test_multibatch_route_cg import enumerate_trips
from tests.routing.test_single_batch_solver_audit import tiny_problem,enumerate_tiny


def independent_trips(p,site,context):
    # The oracle computes Floyd-Warshall and raw earliest clocks/load profiles;
    # it does not call the candidate contexts, replay, label or MIP helpers.
    # Only use its task universe from the independently reviewed domain gate.
    fixed = copy(p)
    eligible = {n['order'].id for n in context['nodes']}
    fixed.orders = tuple(replace(o,satellite=site) for o in p.orders if o.id in eligible)
    fixed.order_map = {o.id:o for o in fixed.orders}
    return enumerate_trips(fixed,site)


def fully_enumerated_joint_lp(p,domains,contexts):
    gp = pytest.importorskip('gurobipy')
    m = gp.Model('independent_all_routes_M3_M4');m.Params.OutputFlag = 0;m.Params.Threads = 1
    try:
        variables,columns = {},{}
        for s,c in contexts.items():
            columns[s] = independent_trips(p,s,c)
            variables[s] = [m.addVar(obj=cost) for _,cost in columns[s]]
        if p.free_assignment:
            # Shared customer/warehouse assignment for every order's BOTH kinds.
            z = {(c,s):m.addVar() for c,sites in domains.items() for s in sites}
            for c,sites in domains.items():m.addConstr(gp.quicksum(z[c,s] for s in sites)==1)
        for s,context in contexts.items():
            for node in context['nodes']:
                m.addConstr(gp.quicksum(v for v,(seq,_) in zip(variables[s],columns[s]) if node['key'] in seq)
                    == (z[node['order'].customer,s] if p.free_assignment else 1))
            m.addConstr(gp.quicksum(variables[s])<=p.fleet.e2_per_sat_count*p.operating_policy.e2_max_trips_per_vehicle)
        m.optimize()
        return m.ObjVal if m.Status==gp.GRB.OPTIMAL else math.inf
    finally:m.dispose()


@pytest.mark.parametrize('model',[3,4])
@pytest.mark.parametrize('seed',range(8))
@pytest.mark.parametrize('heuristic',[False,True])
def test_root_bound_matches_independent_complete_route_lp_and_original_integer_oracle(model,seed,heuristic):
    p = tiny_problem(model,seed);fingerprint = problem_fingerprint(p)
    optimum = enumerate_tiny(p)
    domains,contexts,check = pickup_contexts(p)
    result = root_pickup_route_bound(p,time_limit=5,heuristic_columns=heuristic)
    assert not result['proven_optimal'] and not result['proven_infeasible']
    assert result['all_task_count']==2*len(p.orders) and problem_fingerprint(p)==fingerprint
    assert all(h['corrected_e2_bound'] is None for h in result['heuristic_history'])
    if math.isfinite(optimum):
        expected = fully_enumerated_joint_lp(p,domains,contexts)
        assert result['relaxation_complete'],result
        assert result['restricted_e2_objective']==pytest.approx(expected,abs=1e-6)
        assert result['e2_bound']<=expected+1e-7
        assert result['bound']<=optimum+1e-7
        for history in result['history']:
            if history['phase']==2:
                assert result['e1_floor']+max(0,history['corrected_e2_bound'])<=optimum+1e-7


@pytest.mark.parametrize('model',[3,4])
@pytest.mark.parametrize('phase',[False,True])
@pytest.mark.parametrize('seed',range(6))
def test_reused_integer_pricing_matches_independent_all_task_sequences(model,phase,seed):
    p = tiny_problem(model,seed)
    _,contexts,_ = pickup_contexts(p);rng = random.Random(89+seed)
    for site,ctx in contexts.items():
        oracle = independent_trips(p,site,ctx)
        if not oracle:continue
        pricer = RoutePricer(p,ctx,seed=42)
        try:
            for revision in range(2):
                pi = {n['key']:rng.uniform(-5,25) for n in ctx['nodes']};nu = -2.5
                minimum = min((0 if phase else cost)-sum(pi[k] for k in seq)-nu for seq,cost in oracle)
                priced = pricer.price(pi,nu,time_limit=3,phase_one=phase)
                assert priced['optimal'] and priced['reduced_cost']==pytest.approx(minimum,abs=1e-6)
                assert priced['lower_bound']<=minimum+1e-7
        finally:pricer.dispose()


@pytest.mark.parametrize('model',[3,4])
@pytest.mark.parametrize('seed',[0,1,4,5])
def test_original_integer_schedule_projects_all_tasks_and_raw_arcs(model,seed):
    p = tiny_problem(model,seed)
    result = solve_exact(p,time_limit=3)
    assert result['info']['proven_optimal'] and audit_solution(p,result)['valid']
    _,contexts,_ = pickup_contexts(p)
    columns = project_original_schedule(p,result,contexts)
    assert sum(len(c['tasks']) for _,c in columns)==2*len(p.orders)
    cert = root_pickup_route_bound(p,time_limit=4,initial_result=result)
    assert cert['bound']<=result['info']['objective']+1e-7


def test_cross_trip_physical_vehicle_schedule_projects_without_becoming_an_integer_proof():
    from tests.routing.test_single_batch_primal import split_problem
    from models.routing.model_4.exact.initial_solution import construct_incumbent
    p = split_problem();start = construct_incumbent(p,time.perf_counter()+1)
    assert start and audit_solution(p,start)['valid']
    _,contexts,_ = pickup_contexts(p)
    projected = project_original_schedule(p,start,contexts)
    assert len(projected)>1
    assert Counter(k for _,c in projected for k in c['tasks'])==Counter((o.id,k) for o in p.orders for k in ('d','p'))
    cert = root_pickup_route_bound(p,time_limit=4,initial_result=start)
    assert cert['bound']<=start['info']['objective']+1e-7 and not cert['proven_optimal']


@pytest.mark.parametrize('model',[3,4])
def test_duplicate_location_remains_separate_delivery_pickup_order_tasks(model):
    base = tiny_problem(model,0)
    orders = [base.orders[0],replace(base.orders[1],customer=base.orders[0].customer)]
    p = MultiBatchInstance(base.network,orders,base.operating_policy,model=model)
    domains,contexts,_ = pickup_contexts(p)
    if model==3:
        assert Counter(n['key'] for c in contexts.values() for n in c['nodes'])==Counter((o.id,k) for o in orders for k in ('d','p'))
    else:
        for ctx in contexts.values():assert len(ctx['nodes'])==4
    expected = fully_enumerated_joint_lp(p,domains,contexts)
    result = root_pickup_route_bound(p,time_limit=3)
    assert result['relaxation_complete'] and result['restricted_e2_objective']==pytest.approx(expected)


@pytest.mark.parametrize('model',[3,4])
@pytest.mark.parametrize('heuristic',[False,True])
def test_exhausted_budget_never_promotes_restricted_value_or_old_proof(model,heuristic):
    p = tiny_problem(model,1);oracle = enumerate_tiny(p)
    result = root_pickup_route_bound(p,time_limit=1e-6,heuristic_columns=heuristic)
    assert result['bound']<=oracle+1e-7 and not result['proven_optimal'] and not result['relaxation_complete']


@pytest.mark.parametrize('model',[1,2])
def test_delivery_only_problem_is_not_silently_reinterpreted(model):
    with pytest.raises(ValueError,match='M3/M4'):root_pickup_route_bound(tiny_problem(model,1))


@pytest.mark.parametrize('flag',[1,'yes',None])
def test_column_discovery_flag_requires_an_explicit_boolean(flag):
    with pytest.raises(ValueError,match='boolean'):
        root_pickup_route_bound(tiny_problem(4,1),heuristic_columns=flag)
