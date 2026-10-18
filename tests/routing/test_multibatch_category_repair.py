"""Full-domain batch prices can be repaired without a K-fold penalty."""
import math
import random
import time

import pytest

from models.routing.model_5.exact.accelerators.multibatch.route_cg import repair_category_duals, route_cover_certificate
from models.routing.model_5.exact.accelerators.pickup_route_pricing import route_context, evaluate_column
from models.routing.model_5.exact.accelerators.multibatch.route_pool import candidate_pools, reoptimize_route_pool
from tests.routing.test_multibatch_route_cg import enumerate_trips, enumerated_lp
from tests.routing.test_multibatch_departure_loads import nonmetric_problem
from tests.routing.test_multibatch_joint_exchanges import full_slot_fixture


@pytest.mark.parametrize('seed', [0, 2, 5])
def test_repaired_prices_cover_every_independently_enumerated_trip(seed):
    pytest.importorskip('gurobipy')
    p=nonmetric_problem(seed);rng=random.Random(700+seed)
    for site in p.satellites:
        context=route_context(p,site)
        if not context['nodes']:continue
        columns=enumerate_trips(p,site)
        optimum=enumerated_lp(p,site,columns)
        for _ in range(12):
            pi={n['key']:rng.uniform(-30,30) for n in context['nodes']};nu=-rng.uniform(0,5)
            reports=[]
            for batch in [None]+sorted({n['order'].batch for n in context['nodes'] if n['key'][1]=='d'}):
                eligible=[(seq,cost) for seq,cost in columns if
                    {p.order_map[oid].batch for oid,kind in seq if kind=='d'}==({batch} if batch else set())]
                rho=min((cost-math.fsum(pi[k] for k in seq)-nu for seq,cost in eligible),default=0)
                # Weaken the exact oracle to simulate unfinished pricing.
                reports.append(dict(category=batch,lower_bound=rho-rng.uniform(0,3),infeasible=not eligible))
            fixed=repair_category_duals(context,pi,nu,dict(groups=reports))
            updated={tuple(k):v for k,v in fixed['repaired_duals']}
            assert fixed['repaired_fleet_dual']<=0
            assert all(cost-math.fsum(updated[k] for k in seq)-fixed['repaired_fleet_dual']>=-1e-7 for seq,cost in columns)
            assert fixed['bound']<=optimum+1e-7
            standard=math.fsum(pi.values())+context['maximum_trips']*nu+context['maximum_trips']*min(0,min(g['lower_bound'] for g in reports if not g['infeasible']))
            assert fixed['bound']>=standard-1e-7


def test_dual_repair_rejects_missing_categories_and_task_prices():
    p=nonmetric_problem(0);site=next(s for s in p.satellites if route_context(p,s)['nodes'])
    c=route_context(p,site);pi={n['key']:0 for n in c['nodes']}
    with pytest.raises(ValueError,match='all delivery-batch'):
        repair_category_duals(c,pi,0,dict(groups=[]))
    groups=[dict(category=b,lower_bound=0,infeasible=False) for b in {None}|{n['order'].batch for n in c['nodes'] if n['key'][1]=='d'}]
    with pytest.raises(ValueError,match='every task'):
        repair_category_duals(c,{},0,dict(groups=groups))
    groups[0]['lower_bound']=math.nan
    with pytest.raises(ValueError,match='finite'):
        repair_category_duals(c,pi,0,dict(groups=groups))


def test_public_batch_pricing_and_dual_repair_match_complete_small_lp():
    pytest.importorskip('gurobipy')
    p=nonmetric_problem(2)
    result=route_cover_certificate(p,time_limit=5,batch_pricing=True,dual_repair=True,heuristic_pricing=False)
    assert result['status']=='LP_CONVERGED'
    assert not result['proven_optimal'] and not result['proven_infeasible']
    witnesses=[]
    for row in result['sites']:
        assert row['bound']==pytest.approx(enumerated_lp(p,row['satellite'],enumerate_trips(p,row['satellite'])),abs=1e-3)
        witnesses += [w for w in row['iterations'] if w['method']=='integer_pricing']
    assert any(len(w['new_columns'])>1 for w in witnesses)
    assert any('dual_repair' in w for w in witnesses if w['phase']=='distance')


def test_pair_recombination_unlocks_full_slots_and_keeps_originals():
    pytest.importorskip('gurobipy')
    p,initial=full_slot_fixture()
    pools=candidate_pools(p,initial,exchanges=False,recombinations=True,deadline=time.perf_counter()+2,max_columns=256)
    desired=[frozenset((o,k) for o in ids for k in ('d','p')) for ids in [('A','B'),('C','E')]]
    for trip,rows,wanted in zip(initial['e2_trips'],pools,desired):
        original=tuple((e['order_id'],e['kind']) for e in trip['events'])
        assert rows[0]['tasks']==original
        assert wanted in {frozenset(c['tasks']) for c in rows}
        for column in rows:
            assert evaluate_column(p,route_context(p,trip['satellite_id']),column['tasks'])==column
    result=reoptimize_route_pool(p,initial,time_limit=5,exchanges=False,recombinations=True,max_columns=256)
    assert result['info']['objective']==pytest.approx(8)
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
    assert result['info']['route_pool_recombinations']
    assert result['info']['best_bound'] is None and not result['info']['proven_optimal']
    limited=candidate_pools(p,initial,recombinations=True,deadline=time.perf_counter()-1,max_columns=1)
    assert all(len(row)==1 for row in limited)
