"""Automatic separation must preserve independent complete M5 optima."""
from copy import deepcopy
from dataclasses import replace
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact import multibatch_logic_benders as lbbd
from models.routing.solvers.exact.multibatch_joint_inventory import build_joint_inventory,set_joint_projection
from models.routing.solvers.exact.multibatch_physical_cuts import add_physical_cut,project_physical_costs
from models.routing.solvers.exact.solver import solve_exact
from tests.routing.test_multibatch_cycle import fixture,hand_solution
from tests.routing.test_multibatch_departure_loads import nonmetric_problem
from tests.routing.test_multibatch_joint_inventory import domains
from tests.routing.test_multibatch_route_service import congestion_problem


@pytest.mark.parametrize('seed',range(6))
def test_independent_full_optimum_bounds_and_projects_into_every_automatic_cut(seed,tmp_path):
    gp=pytest.importorskip('gurobipy')
    problem=nonmetric_problem(seed)
    initial=solve_exact(problem,time_limit=5)
    assert initial['info']['proven_optimal'] and audit_solution(problem,initial)['valid']
    before=deepcopy(initial)
    result=lbbd.logic_benders_certificate(problem,time_limit=6,initial_result=initial,
        max_rounds=3,oracle_time_limit=1.5,master_time_limit=1,reduction_checks=2,diagnostics_dir=tmp_path)
    assert result['complete_domain'] and 0 <= result['bound'] <= initial['info']['objective']+1e-6
    assert result['upper_bound']==pytest.approx(initial['info']['objective'])
    assert not result['proven_optimal'] and not result['proven_infeasible']
    assert result['result']==before and initial==before
    model,context=build_joint_inventory(problem,domains(problem,timed=True))
    try:
        cuts=[add_physical_cut(problem,model,context,c['certificate'],name='test_'+str(i))
              for i,c in enumerate(result['cuts'])]
        for v in context['choices']:v.VType=gp.GRB.BINARY
        set_joint_projection(problem,context,initial,fix=True)
        if cuts:project_physical_costs(problem,context,initial,cuts=cuts,fix=True)
        model.optimize()
        assert model.Status==gp.GRB.OPTIMAL and model.ObjVal<=initial['info']['objective']+1e-6
    finally:model.dispose()


def test_calendar_conflict_is_automatically_detected_and_resume_uses_frozen_proofs(tmp_path):
    pytest.importorskip('gurobipy');p=congestion_problem()
    initial=solve_exact(p,time_limit=5)
    control=lbbd.logic_benders_certificate(p,time_limit=8,initial_result=initial,max_rounds=0)
    from models.routing.variants.model_5.exact import logic_benders_certificate
    treatment=logic_benders_certificate(p,time_limit=8,initial_result=initial,
        oracle_time_limit=2,reduction_checks=8,diagnostics_dir=tmp_path)
    assert control['rounds'][0]['raw_bound']==pytest.approx(8)
    assert treatment['bound']>9.99 and treatment['bound']<=10
    assert any(c['certificate']['subproblem_infeasible'] for c in treatment['cuts'])
    assert all(not e['minimality_proven'] for e in treatment['events'] if e['kind']=='reduction')
    again=lbbd.logic_benders_certificate(p,time_limit=8,initial_result=initial,
        oracle_time_limit=2,reduction_checks=8,diagnostics_dir=tmp_path)
    assert again['bound']>=treatment['bound']-1e-6
    assert again['cache']['hits']>0 and any(c['resumed'] for c in again['cuts'])
    assert Path(treatment['run_directory'])!=Path(again['run_directory'])
    assert (Path(treatment['run_directory'])/'result.json').exists()
    assert json.loads((tmp_path/'checkpoint.json').read_text())['run_directory']==again['run_directory']


def test_unknown_oracle_does_not_create_cut_or_cache(monkeypatch,tmp_path):
    pytest.importorskip('gurobipy');p=congestion_problem()
    initial=solve_exact(p,time_limit=5)
    def unknown(problem,site,**kwargs):
        return dict(status=9,bound=999,runtime=0,subproblem_optimal=False,subproblem_infeasible=False)
    monkeypatch.setattr(lbbd,'physical_site_certificate',unknown)
    r=lbbd.logic_benders_certificate(p,time_limit=5,initial_result=initial,diagnostics_dir=tmp_path)
    assert r['cuts']==[] and r['bound']<8.001
    assert not list(Path(r['cache']['directory']).glob('*.json'))
    assert r['status']=='NO_NEW_VERIFIED_CUT'


def test_corrupted_certificate_or_mps_is_not_reused(tmp_path):
    pytest.importorskip('gurobipy');p=congestion_problem()
    initial=solve_exact(p,time_limit=5)
    r=lbbd.logic_benders_certificate(p,time_limit=6,initial_result=initial,reduction_checks=0,diagnostics_dir=tmp_path)
    cache=lbbd._CertificateCache(tmp_path/'oracle_cache',r['identity'])
    record=next(cache.records());path=cache.path(record['request'])
    saved=path.read_bytes();altered=json.loads(saved)
    altered['certificate']['bound']=12345
    path.write_text(json.dumps(altered))
    assert cache.load(record['request']) is None
    path.write_bytes(saved)
    proof=cache.directory/record['proof_file'];proof.write_bytes(proof.read_bytes()+b'corrupt')
    assert cache.load(record['request']) is None and cache.rejected==2


@pytest.mark.parametrize('change',['stock','vehicle','capacity','window','distance','travel','service'])
def test_business_fingerprint_invalidates_each_changed_physical_input(change):
    p=nonmetric_problem(0);before=lbbd.business_fingerprint(p)
    if change=='stock':p=MultiBatchInstance(p.network,p.orders,replace(p.operating_policy,container_pool_size=5))
    elif change=='vehicle':p.fleet.e2_per_sat_count+=1
    elif change=='capacity':p.fleet.e2_weight_cap+=1
    elif change=='window':p=MultiBatchInstance(p.network,[replace(p.orders[0],delivery_window=(0,24))]+list(p.orders[1:]),p.operating_policy)
    elif change=='distance':p.network.dist[0][1]+=.125
    elif change=='travel':p._travel[next(iter(p._travel))]+=1
    elif change=='service':p.network.nodes[p.satellites[0]].service_time+=1
    assert before!=lbbd.business_fingerprint(p)


def test_fractional_selected_routes_are_rejected():
    p=fixture(pool=2)
    context=dict(routes={'S':dict(choices={(0,None):SimpleNamespace(X=.5)},rows=[dict(keys=[('o1','d')])])})
    with pytest.raises(ValueError,match='Fractional'):lbbd.selected_conditions(p,context)


def test_unknown_conflict_deletion_retains_last_verified_proof():
    p=congestion_problem()
    request=lbbd._request(p,'S',[[('a','d'),('a','p')],[('b','d'),('b','p')]])
    record=dict(request=request,certificate=dict(subproblem_infeasible=True))
    calls=[]
    def query(trial):calls.append(trial);return None
    core,stats=lbbd.shrink_conflict(p,record,query,max_checks=2)
    assert core is record and stats['retained_atoms']==2 and len(calls)==2
    assert not stats['minimality_proven']


def test_incomplete_domain_never_produces_a_new_bound(tmp_path):
    pytest.importorskip('gurobipy');p=nonmetric_problem(0);initial=solve_exact(p,time_limit=5)
    r=lbbd.logic_benders_certificate(p,time_limit=3,initial_result=initial,
                                   enumeration_max_states=1,diagnostics_dir=tmp_path)
    assert not r['complete_domain'] and r['bound']==0 and not r['cuts']
    assert r['result']==initial and r['status']=='INCOMPLETE_DOMAIN_NO_NEW_BOUND'


@pytest.mark.parametrize('name,value',[('time_limit',0),('time_limit',math.inf),('time_limit',True),
    ('oracle_time_limit',math.nan),('master_time_limit',-1),('recovery_time_limit',-1),
    ('reduction_checks',True),('max_rounds',-1),('enumeration_max_states',0),('timing_envelopes',1),('group_first',1)])
def test_parameter_guards(name,value):
    with pytest.raises(ValueError):lbbd.logic_benders_certificate(fixture(pool=2),**{name:value})


def test_bad_initial_is_rejected_and_budget_build_exit_preserves_good_initial(tmp_path):
    p=fixture(pool=2);initial=hand_solution(p)
    bad=deepcopy(initial);bad['e2_trips'].pop()
    with pytest.raises(ValueError,match='audit'):lbbd.logic_benders_certificate(p,initial_result=bad)
    r=lbbd.logic_benders_certificate(p,time_limit=1e-6,initial_result=initial,diagnostics_dir=tmp_path)
    assert r['bound']==0 and r['result']==initial and not r['complete_domain']


def test_source_version_change_rejects_resume_before_solving(monkeypatch,tmp_path):
    pytest.importorskip('gurobipy');p=fixture(pool=2)
    lbbd.logic_benders_certificate(p,time_limit=3,initial_result=hand_solution(p),max_rounds=0,diagnostics_dir=tmp_path)
    monkeypatch.setattr(lbbd,'source_fingerprint',lambda:'different-source')
    with pytest.raises(ValueError,match='another business/source'):
        lbbd.logic_benders_certificate(p,time_limit=3,diagnostics_dir=tmp_path)


def test_long_compressed_proof_paths_roundtrip_without_native_compressed_io(tmp_path):
    gp=pytest.importorskip('gurobipy')
    path=tmp_path/('x'*120)/('y'*120)/'proof.mps.gz'
    path.parent.mkdir(parents=True)
    with gp.Env(empty=True) as env:
        env.setParam('OutputFlag',0);env.start()
        model=gp.Model(env=env)
        model.addVar(lb=1,ub=1,obj=7);model.update()
        try:lbbd._write_model(model,path)
        finally:model.dispose()
        replay=lbbd._read_model(gp,path,env)
        try:
            replay.optimize();assert replay.ObjVal==pytest.approx(7)
        finally:replay.dispose()


def test_duplicate_cache_worker_is_rejected(tmp_path):
    from contextlib import ExitStack
    with ExitStack() as first,ExitStack() as second:
        lbbd._lock(first,tmp_path/'worker.lock')
        with pytest.raises(RuntimeError,match='already owns'):
            lbbd._lock(second,tmp_path/'worker.lock')


def test_full_group_repair_returns_audited_upper_without_an_initial_solution(tmp_path):
    pytest.importorskip('gurobipy');p=fixture(pool=2)
    independent=solve_exact(p,time_limit=5)
    assert independent['info']['proven_optimal']
    r=lbbd.logic_benders_certificate(p,time_limit=5,recovery_time_limit=1,diagnostics_dir=tmp_path)
    assert r['result'] is not None and audit_solution(p,r['result'])['valid']
    assert r['upper_bound']>=independent['info']['objective']-1e-6
    assert r['bound']<=independent['info']['objective']+1e-6
    assert not r['proven_optimal'] and not r['proven_infeasible']
