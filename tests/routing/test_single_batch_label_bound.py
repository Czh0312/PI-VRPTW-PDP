"""Independent suffix/route enumeration and interrupted dual certificates."""
import itertools
from dataclasses import replace
import math
import random
import time

import pytest

from models.routing.model_3.exact.bounds.route_cg.label_bound import completion_cost_floor
from models.routing.model_3.exact.bounds.route_cg.route_pricing import price_delivery_routes
from models.routing.model_3.exact.bounds.route_cg.columns import root_route_bound
from models.routing.model_3.exact.bounds.route_cg.time_budget import reduced_route_cost
from tests.routing.test_single_batch_columns import contexts,independently_enumerated_trips
from tests.routing.test_single_batch_solver_audit import tiny_problem,enumerate_tiny
from models.common.cycle import MultiBatchInstance


@pytest.mark.parametrize('seed',range(12))
def test_relaxed_return_floor_matches_independent_nonmetric_suffix_enumeration(seed):
    rng = random.Random(seed)
    n = 1+seed%5
    cost = [[rng.randrange(0,40) for _ in range(n)] for _ in range(n)]
    inward = [rng.randrange(0,40) for _ in range(n)]
    floors,complete = completion_cost_floor(cost,inward)
    assert complete
    for first in range(n):
        others = [i for i in range(n) if i!=first]
        paths = [(first,)+path for length in range(n) for path in itertools.permutations(others,length)]
        oracle = min(sum(cost[a][b] for a,b in zip(path,path[1:]))+inward[path[-1]] for path in paths)
        assert floors[first]<=oracle and floors[first]==pytest.approx(oracle,abs=1e-7)


def test_interrupted_shortest_paths_discard_tentative_upper_bounds():
    assert completion_cost_floor([[0,1],[100,0]],[100,1],deadline=time.perf_counter()-1)==([0.0,0.0],False)


def test_partially_settled_shortest_paths_also_discard_all_tentative_distances(monkeypatch):
    from models.routing.model_3.exact.bounds.route_cg import label_bound as module
    ticks = iter([0.0,0.0,2.0])
    monkeypatch.setattr(module.time,'perf_counter',lambda:next(ticks))
    floor,complete = module.completion_cost_floor([[0,1,80],[30,0,1],[30,30,0]],
                                                 [100,100,1],deadline=1.0)
    assert not complete and floor==[0.0,0.0,0.0]


@pytest.mark.parametrize('phase',[False,True])
@pytest.mark.parametrize('penalized',[False,True])
@pytest.mark.parametrize('seed',range(8))
def test_bounded_pricing_matches_independent_elementary_minima(phase,penalized,seed):
    p = tiny_problem(2,seed)
    _,_,by_site = contexts(p)
    rng = random.Random(117+seed)
    for c in by_site.values():
        prices = {n['key']:rng.uniform(-4,20) for n in c['nodes']}
        work = (((0,30),.4),((5,60),.2)) if penalized else ()
        trips = list(independently_enumerated_trips(p,c))
        values = [reduced_route_cost(p,c['satellite'],trip,prices,-2,phase_one=phase,work_prices=work) for trip in trips]
        oracle = min(values,default=math.inf)
        bounded = price_delivery_routes(p,c,prices,-2,phase_one=phase,work_prices=work,bound_pruning=True)
        assert bounded['complete'] and bounded['return_floor_complete']
        if trips:
            assert bounded['minimum_reduced_cost']==pytest.approx(oracle)
            assert bounded['lower_bound']<=oracle+1e-9
        else:
            assert bounded['infeasible']
        expected = {trip['tasks']:trip for trip in trips}
        for column in bounded['columns']:
            assert column['starts']==expected[column['tasks']]['starts']
            assert column['cost']==pytest.approx(expected[column['tasks']]['cost'])


@pytest.mark.parametrize('limit',[1,3,7,20])
@pytest.mark.parametrize('phase',[False,True])
@pytest.mark.parametrize('penalized',[False,True])
def test_every_interruption_bound_is_safe(limit,phase,penalized):
    p = tiny_problem(2,1)
    _,_,by_site = contexts(p)
    work = (((0,30),.4),) if penalized else ()
    for c in by_site.values():
        prices = {n['key']:11+5*i for i,n in enumerate(c['nodes'])}
        oracle = min(reduced_route_cost(p,c['satellite'],trip,prices,-2,phase_one=phase,work_prices=work)
                     for trip in independently_enumerated_trips(p,c))
        for extra in (dict(label_limit=limit),dict(deadline=time.perf_counter()-1),
                      dict(column_limit=1,stop_after_columns=True)):
            result = price_delivery_routes(p,c,prices,-2,phase_one=phase,work_prices=work,bound_pruning=True,**extra)
            assert result['lower_bound']<=oracle+1e-9
            if result['complete']:
                assert result['minimum_reduced_cost']==pytest.approx(oracle)


def test_expensive_infeasible_direct_return_does_not_prune_nonmetric_prefix():
    p = tiny_problem(2,0)
    site,a,b = 'S',p.orders[0].customer,p.orders[1].customer
    p._travel[a,site] = 1000
    p.network.dist[p.network.node_index[a]][p.network.node_index[site]] = 1000
    _,_,by_site = contexts(p)
    c = by_site[site]
    prices = {o.id:100 for o in p.orders}
    trips = list(independently_enumerated_trips(p,c))
    oracle = min(trip['cost']-sum(prices[k] for k in trip['tasks']) for trip in trips)
    result = price_delivery_routes(p,c,prices,bound_pruning=True)
    assert result['complete'] and result['minimum_reduced_cost']==pytest.approx(oracle)
    assert any(len(column['tasks'])==len(p.orders) for column in result['columns'])


def test_frontier_closes_before_enumerating_feasible_positive_cost_routes():
    p = tiny_problem(2,1)
    _,_,by_site = contexts(p)
    pruned = 0
    for c in by_site.values():
        prices = {n['key']:-1 for n in c['nodes']}
        old = price_delivery_routes(p,c,prices)
        new = price_delivery_routes(p,c,prices,bound_pruning=True)
        assert new['minimum_reduced_cost']==pytest.approx(old['minimum_reduced_cost'])
        assert new['expanded_labels']<=old['expanded_labels']
        pruned += old['expanded_labels']-new['expanded_labels']
    assert pruned>0


def test_batch_discovery_keeps_all_possible_negative_columns_before_closure():
    base = tiny_problem(2,1)
    base.fleet.e2_weight_cap = 100
    base.fleet.e2_volume_cap = base.fleet.container_volume
    orders = [replace(o,service_time=0,delivery_window=(0,40)) for o in base.orders]
    p = MultiBatchInstance(base.network,orders,base.operating_policy,model=2)
    for key in p._travel:
        p._travel[key] = 0
    _,_,by_site = contexts(p)
    for c in by_site.values():
        prices = {n['key']:i+1 for i,n in enumerate(c['nodes'])}
        expected = {trip['tasks'] for trip in independently_enumerated_trips(p,c)}
        assert len(expected)==len(c['nodes']) and all(len(tasks)==1 for tasks in expected)
        batch = price_delivery_routes(p,c,prices,phase_one=True,bound_pruning=True,
                                      stop_after_columns=True,column_limit=len(expected)+1)
        single = price_delivery_routes(p,c,prices,phase_one=True,bound_pruning=True)
        assert batch['complete'] and batch['minimum_reduced_cost']==-len(c['nodes'])
        assert {col['tasks'] for col in batch['columns']}==expected
        assert batch['expanded_labels']>single['expanded_labels']


@pytest.mark.parametrize('model',[1,2])
@pytest.mark.parametrize('seed',range(8))
def test_root_bound_is_safe_against_original_integer_oracle(model,seed):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model,seed)
    oracle = enumerate_tiny(p)
    old = root_route_bound(p,time_limit=3)
    new = root_route_bound(p,time_limit=3,label_bound_pruning=True)
    assert not new['proven_optimal']
    if math.isfinite(oracle):
        assert not new['proven_infeasible'] and new['bound']<=oracle+1e-8
        assert new['relaxation_complete'] and old['relaxation_complete']
        assert new['restricted_objective']==pytest.approx(old['restricted_objective'])


@pytest.mark.parametrize('value',[1,None,'on'])
def test_hook_type_and_mip_scope_are_explicit(value):
    p = tiny_problem(2,1)
    _,_,by_site = contexts(p)
    with pytest.raises(ValueError,match='boolean'):
        price_delivery_routes(p,next(iter(by_site.values())),{},bound_pruning=value)
    with pytest.raises(ValueError,match='boolean'):
        root_route_bound(p,label_bound_pruning=value)
    with pytest.raises(ValueError,match='requires label'):
        root_route_bound(p,pricing_method='mip',label_bound_pruning=True)
