"""Independent original integer oracle and guarded E1 row integration."""
from copy import deepcopy
from types import SimpleNamespace
import math
import time

import pytest

from models.common.cycle.audit import audit_solution
from models.routing.model_3.exact.model import _build_complete_model as build_model
from models.routing.model_3.exact.enhanced_solver import solve_exact, _set_start
from models.routing.model_3.exact.bounds.e1 import e1_route_cover_bound, apply_e1_cover_bound, solve_exact_with_e1_cover
from tests.routing.test_single_batch_solver_audit import tiny_problem,enumerate_tiny


@pytest.mark.parametrize('seed',range(8))
def test_complete_kernel_with_row_matches_independent_integer_oracle(seed):
    p=tiny_problem(3,seed);expected=enumerate_tiny(p)
    result=solve_exact_with_e1_cover(p,3,seed=42)
    if math.isfinite(expected):
        assert audit_solution(p,result)['valid'] and result['info']['proven_optimal']
        assert result['info']['objective']==pytest.approx(expected,abs=1e-6)
        assert result['info']['best_bound']<=expected+1e-6
    else:assert result['info']['proven_infeasible']


def test_added_row_uses_only_original_raw_E1_arc_coefficients():
    p=tiny_problem(3,1);c=e1_route_cover_bound(p,1);m,ctx=build_model(p)
    try:
        added=apply_e1_cover_bound(m,p,ctx,c);m.update();assert added['applied']
        row=m.getRow(m.getConstrByName('B1_M3_certified_E1_distance_floor'))
        actual={row.getVar(i).VarName:row.getCoeff(i) for i in range(row.size())}
        expected={}
        for route in ctx['e1']:
            for (a,b),v in route['x'].items():
                left=p.depot if a<0 else route['nodes'][a]['site']
                right=p.depot if b<0 else route['nodes'][b]['site']
                cost=p.distance(left,right)
                if cost:expected[v.VarName]=cost
        assert actual==pytest.approx(expected)
        assert not set(actual)&{v.VarName for r in ctx['e2'] for v in r['x'].values()}
    finally:m.dispose()


@pytest.mark.parametrize('seed',[0,1,4,5])
def test_fixed_original_schedule_satisfies_actual_new_row_without_altered_arcs(seed):
    p=tiny_problem(3,seed);original=solve_exact(p,3);a=audit_solution(p,original)
    assert a['valid'];c=e1_route_cover_bound(p,1);m,ctx=build_model(p)
    try:
        apply_e1_cover_bound(m,p,ctx,c);assert _set_start(p,m,ctx,original);m.update()
        raw=sum(p.distance(p.depot if x<0 else r['nodes'][x]['site'],
            p.depot if y<0 else r['nodes'][y]['site'])*v.Start for r in ctx['e1'] for (x,y),v in r['x'].items())
        assert raw==pytest.approx(a['distances']['e1'],abs=1e-6) and raw>=c['bound']-1e-6
        result=solve_exact_with_e1_cover(p,3,initial_result=original)
        assert audit_solution(p,result)['valid'] and result['info']['objective']==pytest.approx(a['objective'])
    finally:m.dispose()


@pytest.mark.parametrize('field,value',[
    ('proof_scope','wrong'),('problem_sha256','wrong'),('bound',True),
    ('bound',-1),('bound',math.inf),('bound',math.nan),('bound','5'),('bound',None)])
def test_malformed_private_certificate_rejected_before_row_mutation(field,value):
    gp=pytest.importorskip('gurobipy');p=tiny_problem(3,1);c=e1_route_cover_bound(p,1);c[field]=value
    m=gp.Model();m.Params.OutputFlag=0
    try:
        with pytest.raises(ValueError):apply_e1_cover_bound(m,p,{'e1':[]},c)
        m.update();assert m.NumConstrs==0
    finally:m.dispose()


@pytest.mark.parametrize('model',[1,2,4])
def test_new_hook_never_applies_fixed_assignment_floor_to_other_models(model):
    p=tiny_problem(model,1);c=e1_route_cover_bound(tiny_problem(3,1),1)
    with pytest.raises(ValueError,match='M3'):solve_exact(p,1,_single_batch_e1_certificate=c)
    with pytest.raises(ValueError,match='M3'):solve_exact_with_e1_cover(p,1)


def test_application_deadline_skips_row_and_preserves_valid_certificate():
    p=tiny_problem(3,1);c=e1_route_cover_bound(p,1);m,ctx=build_model(p)
    try:
        before=m.NumConstrs;applied=apply_e1_cover_bound(m,p,ctx,c,deadline=time.perf_counter()-1)
        m.update();assert not applied['applied'] and m.NumConstrs==before
    finally:m.dispose()


def test_exhausted_wrapper_budget_retains_original_warm_start_without_old_optimal_flag(monkeypatch):
    import models.routing.model_3.exact.bounds.e1 as module
    p=tiny_problem(3,1);warm=solve_exact(p,3);c=e1_route_cover_bound(p,1);clock=[0.0]
    def stage(*args,**kwargs):clock[0]=2.0;return deepcopy(c)
    monkeypatch.setattr(module,'time',SimpleNamespace(perf_counter=lambda:clock[0]))
    monkeypatch.setattr(module,'e1_route_cover_bound',stage)
    result=module.solve_exact_with_e1_cover(p,1,initial_result=warm)
    assert audit_solution(p,result)['valid'] and result['e1_trips']==warm['e1_trips'] and result['e2_trips']==warm['e2_trips']
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']
    assert result['info']['status']=='TIME_LIMIT_E1_COMPONENT'
