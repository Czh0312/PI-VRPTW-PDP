"""Fixed-price certificates checked against a complete elementary-trip oracle."""
from copy import deepcopy
import math
import random
import time

import pytest

from models.routing.solvers.exact.multibatch_pricing_polish import refine_pricing_witness
from models.routing.solvers.exact.multibatch_route_pricing import route_context
from tests.routing.test_multibatch_hybrid import capacity_problem
from tests.routing.test_multibatch_route_cg import enumerate_trips


@pytest.mark.parametrize('seed', [0,1,2])
def test_fixed_dual_price_matches_full_domain_oracle_with_capacity_constants(seed,tmp_path):
    pytest.importorskip('gurobipy')
    p=capacity_problem();context=route_context(p,'S');rng=random.Random(950+seed)
    pi={n['key']:rng.uniform(-3,8) for n in context['nodes']};nu=-1.5
    keys={('A','d'),('B','d')};prize=2.3;subset_prize=1.7
    # Wrong stored RHS values deliberately verify that coefficients are
    # rebuilt from physical capacity rather than trusted as external floors.
    old=dict(best_witness=0,iterations=[dict(phase='distance',master_objective=999,
        duals=[[list(k),v] for k,v in pi.items()],fleet_dual=nu,
        delivery_counts=[['b1',999,prize]],subset_counts=[[0,[list(k) for k in sorted(keys)],999,subset_prize]])])
    saved=deepcopy(old)
    leaf=refine_pricing_witness(p,context,old,deadline=time.perf_counter()+5,
                               pricing_export_path=tmp_path/'price.mps')
    witness=leaf['iterations'][0];oracle=enumerate_trips(p,'S')
    def reduced(seq,cost):
        return cost-math.fsum(pi[k] for k in seq)-nu-(prize if any(k=='d' for _,k in seq) else 0)\
            -(subset_prize if keys.intersection(seq) else 0)
    expected=min(reduced(seq,cost) for seq,cost in oracle)
    assert witness['pricing']['optimal']
    assert witness['pricing']['reduced_cost']==pytest.approx(expected,abs=1e-6)
    assert witness['pricing']['lower_bound']<=expected+1e-7
    assert witness['delivery_counts']==[['b1',2,prize]]
    assert witness['subset_counts'][0][2]==1
    repaired={tuple(k):v for k,v in witness['dual_repair']['repaired_duals']}
    repaired_nu=witness['dual_repair']['repaired_fleet_dual']
    for seq,cost in oracle:
        assert cost-math.fsum(repaired[k] for k in seq)-repaired_nu\
            -(prize if any(k=='d' for _,k in seq) else 0)\
            -(subset_prize if keys.intersection(seq) else 0)>=-1e-6
    assert leaf['bound']==max(0,witness['candidate_bound'])
    assert not leaf['lp_converged'] and old==saved


def test_expired_deadline_does_not_build_pricer_or_promote_previous_bound(monkeypatch):
    import models.routing.solvers.exact.multibatch_route_pricing as pricing
    p=capacity_problem();ctx=route_context(p,'S')
    old=dict(bound=1000,best_witness=0,iterations=[dict(phase='distance',
        duals=[[list(n['key']),0] for n in ctx['nodes']],fleet_dual=0)])
    monkeypatch.setattr(pricing,'RoutePricer',lambda *a,**kw:pytest.fail('expired deadline'))
    out=refine_pricing_witness(p,ctx,old,deadline=0)
    assert out['bound']==0 and out['status']=='TIME_LIMIT_BUILD' and out['best_witness'] is None


@pytest.mark.parametrize('old',[{},dict(best_witness=0,iterations=[dict(phase='feasibility')])])
def test_missing_distance_witness_is_rejected(old):
    p=capacity_problem()
    with pytest.raises(ValueError,match='distance-pricing'):
        refine_pricing_witness(p,route_context(p,'S'),old,deadline=0)


def test_unknown_batch_cannot_invent_a_minimum_route_constant():
    p=capacity_problem();ctx=route_context(p,'S')
    old=dict(best_witness=0,iterations=[dict(phase='distance',
        duals=[[list(n['key']),0] for n in ctx['nodes']],fleet_dual=0,
        delivery_counts=[['missing',1,5]])])
    with pytest.raises(ValueError,match='Unknown delivery batch'):
        refine_pricing_witness(p,ctx,old,deadline=0)


def test_adaptive_fixed_prices_preserve_previous_stronger_floor_and_reference(monkeypatch):
    from models.routing.solvers.exact.multibatch_hybrid import finish_hybrid
    from tests.routing.test_multibatch_cycle import fixture,hand_solution
    import models.routing.solvers.exact.multibatch_route_pool as pool
    import models.routing.solvers.exact.multibatch_pricing_polish as polish
    p=fixture(pool=2);initial=hand_solution(p)
    row=dict(satellite='S',bound=4,lp_converged=False,columns=[],best_witness=0,
             iterations=[dict(phase='distance',method='integer_pricing',master_objective=4,
                              pricing=dict(optimal=False,infeasible=False))])
    ref=dict(bound=44,e1_bound=40,sites=[row],subset_runs=[],components=[dict(satellite='S',bound=4)])
    before=deepcopy(ref);calls=[]
    def price(p,ctx,old,**kw):
        calls.append(kw['deadline'])
        return dict(satellite='S',bound=3,lp_converged=False,columns=[],subset_cuts=[],iterations=[])
    monkeypatch.setattr(polish,'refine_pricing_witness',price)
    monkeypatch.setattr(pool,'reoptimize_route_pool',lambda p,r,**kw:r)
    out=finish_hybrid(p,ref,None,initial,deadline=time.perf_counter()+1,
                      fallback_bound=44,target_gap=0,adaptive_pricing_mode='fixed_duals')
    # Use a deliberately lower fallback to request continuation while the
    # reference floor remains certified at 44.
    out=finish_hybrid(p,ref,None,initial,deadline=time.perf_counter()+1,
                      fallback_bound=43,target_gap=0,adaptive_pricing_mode='fixed_duals')
    assert len(calls)==1 and out['bound']==44 and ref==before


def test_selector_ignores_completed_best_and_uses_last_unfinished_distance_price():
    from models.routing.solvers.exact.multibatch_pricing_polish import select_unfinished_witness
    rows=[dict(phase='distance',method='integer_pricing',pricing=dict(optimal=True)),
          dict(phase='distance',method='integer_pricing',pricing=dict(optimal=False,infeasible=False)),
          dict(phase='distance',method='insertion')]
    assert select_unfinished_witness(dict(best_witness=0,iterations=rows))==1
    rows[1]['pricing']['optimal']=True
    assert select_unfinished_witness(dict(best_witness=0,iterations=rows)) is None
    rows[1]['pricing'].update(optimal=False,infeasible=True)
    assert select_unfinished_witness(dict(iterations=rows)) is None
    assert select_unfinished_witness(dict(iterations=[])) is None


@pytest.mark.parametrize('mode',['missing',None])
def test_unknown_public_adaptive_mode_is_rejected(mode):
    from models.routing.solvers.exact.multibatch_hybrid import solve_hybrid
    from tests.routing.test_multibatch_cycle import fixture
    with pytest.raises(ValueError,match='adaptive_pricing_mode'):
        solve_hybrid(fixture(pool=2),adaptive_pricing_mode=mode)
