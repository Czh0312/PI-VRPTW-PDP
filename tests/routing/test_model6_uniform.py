"""The equal calendar is a decision domain, not a count of active delivery slots."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from models.common.cycle.uniform import UniformCycleInstance
from models.common.cycle.joint import JointBatchInstance, joint_empty_result
from models.common.cycle.audit import audit_solution
from tests.routing.test_model6_joint import joint_fixture, exact_available, solve_exact, solve_alns


def uniform_fixture(pool_rate=5,counts=(1,2),**options):
    source = joint_fixture(pool_rate=pool_rate)
    return UniformCycleInstance(source.network,source.orders,source.operating_policy,
                    dict(schedule_mode='uniform_cycle',batch_count_candidates=list(counts),**options),
                    source.cost_profile.config)


def test_k_repartitions_entire_cycle_and_preserves_physical_inputs():
    p = uniform_fixture()
    for k in (1,2):
        child = p.for_count(k)
        assert {b.end-b.start for b in child.operating_policy.batches} == {130//k}
        assert child.operating_policy.batches[-1].end == p.operating_policy.cycle_end
        assert child.operating_policy.cleaning_duration == p.operating_policy.cleaning_duration
        assert child.network is p.network and child._travel is p._travel
        for a,b in zip(p.orders,child.orders):
            for field in ('id','count','goods_weight','delivery_window','pickup_window','release_time','min_use_time'):
                assert getattr(a,field) == getattr(b,field)
        assert len({child.default_cutoffs[b.id]-b.start for b in child.operating_policy.batches}) == 1
    # Historical source slots ended at 120, leaving 10 minutes; the new
    # partition includes all 130 minutes, with terminal cleaning still enforced.
    assert p.for_count(2).operating_policy.batches[1].start == 65


def test_joint_k_choice_changes_with_pool_charge(exact_available):
    low = solve_exact(uniform_fixture(),time_limit=10)
    high = solve_exact(uniform_fixture(pool_rate=30),time_limit=10)
    assert low['audit']['valid'] and high['audit']['valid']
    assert low['info']['proven_optimal'] and high['info']['proven_optimal']
    assert low['cycle_batch_count']==1 and low['container_pool_size']==2
    assert high['cycle_batch_count']==2 and high['container_pool_size']==1
    assert high['batch_duration_ticks']==65
    assert len({b['cutoff']-b['start'] for b in high['batches']})==1
    assert high['audit']['periodic_replay']['valid']


def test_shared_milp_matches_exhaustive_counts_and_offsets(exact_available):
    source = joint_fixture(zero=True,cleaning=1)
    policy = replace(source.operating_policy,cycle_end=12,
                     batches=tuple(replace(b,start=i*6,cutoff=i*6+3,end=(i+1)*6)
                                   for i,b in enumerate(source.operating_policy.batches)))
    orders = [replace(o,delivery_window=(0,8),pickup_window=(0,9)) for o in source.orders]
    p = UniformCycleInstance(source.network,orders,policy,
                             dict(schedule_mode='uniform_cycle',batch_count_candidates=[1,2]),source.cost_profile.config)
    full = solve_exact(p,time_limit=10)
    objectives=[]
    for k in (1,2):
        child=p.for_count(k)
        for offset in range(1,12//k):
            fixed_policy=replace(child.operating_policy,batches=tuple(replace(b,cutoff=b.start+offset)
                                                                      for b in child.operating_policy.batches))
            fixed=JointBatchInstance(child.network,child.orders,fixed_policy,{},p.cost_profile.config)
            result=solve_exact(fixed,time_limit=3)
            assert result['info']['proven_optimal'] or result['info']['proven_infeasible']
            if result.get('audit',{}).get('valid'):
                objectives.append(result['info']['objective'])
    assert full['info']['proven_optimal'] and full['audit']['valid']
    assert full['info']['objective']==pytest.approx(min(objectives))


def test_one_k_optimum_does_not_prove_all_k(monkeypatch,exact_available):
    p=uniform_fixture()
    known=solve_exact(p.for_count(1),time_limit=3)
    from models.routing.model_6.exact import solver
    def partial(child,**kwargs):
        if len(child.batch_map)==1:
            return deepcopy(known)
        empty=joint_empty_result(child,'exact')
        empty['info'].update(status='TIME_LIMIT',proven_infeasible=False,best_bound=0.)
        return empty
    monkeypatch.setattr(solver,'solve_exact',partial)
    from models.routing.model_6.exact.batch_search import solve_uniform
    result=solve_uniform(p,solve_candidate=solver.solve_exact,method='exact',budget=1,seed=42)
    assert result['audit']['valid'] and result['info']['selected_count_proven_optimal']
    assert not result['info']['proven_optimal']
    assert result['info']['best_bound']==0


@pytest.mark.parametrize('mutation',[
    lambda r:r.update(cycle_batch_count=3),
    lambda r:r.update(batch_duration_ticks=1),
    lambda r:r.update(shared_cutoff_offset_ticks=1),
    lambda r:r.update(shared_cutoff_ratio=.999),
    lambda r:r['uniform_cycle_config'].update(batch_count_candidates=[1,2]),
    lambda r:r['batch_cutoffs'].update(b2=r['batch_cutoffs']['b2']+1),
    lambda r:r['batches'][1].update(end=r['batches'][1]['end']-1),
    lambda r:r.update(actual_recovery_round_count=999),
])
def test_uniform_contract_and_cutoffs_cannot_be_forged(mutation):
    p=uniform_fixture(counts=(2,))
    result=solve_alns(p,max_time=.25,max_iterations=50)
    assert result['audit']['valid']
    mutation(result)
    assert not audit_solution(p,result)['valid']


def test_shared_cutoff_moves_never_break_equality():
    from models.routing.model_6.alns.solver import _cutoff_move
    import random
    child=uniform_fixture(counts=(2,)).for_count(2)
    state=dict(deliveries={},pickup_order={},pickup_vehicle={},e2=[],cutoffs=child.default_cutoffs)
    rng=random.Random(42)
    for _ in range(40):
        state=_cutoff_move(child,None,{},state,rng)
        child.validated_cutoffs(state['cutoffs'])
    cuts=dict(state['cutoffs']); cuts['b2']+=1
    with pytest.raises(ValueError,match='same relative'):
        child.validated_cutoffs(cuts)


def test_multi_satellite_recovery_is_counted_once_per_batch(tmp_path,exact_available):
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from tools.data.build_model6_static_suite import build_suite
    manifest=build_suite(tmp_path/'data',families=['mechanism_cutoff_choice'])
    case=json.loads(manifest.read_text(encoding='utf-8'))['cases'][0]['m6_case']
    config,fleet=load_routing_case(case,use_pool_config=False)
    config['joint_batch_options']={'schedule_mode':'uniform_cycle','batch_count_candidates':[1]}
    problem=load_instance(config,fleet)
    result=solve_exact(problem,time_limit=5)
    assert result['audit']['valid'] and result['info']['proven_optimal']
    assert len([r for r in result['recovery_rounds'] if r['quantity']>0])==2
    assert result['actual_recovery_round_count']==1
    assert result['actual_recovery_round_count']<=result['cycle_batch_count']


def test_supplied_uniform_incumbent_is_retained_and_proofs_cleared(monkeypatch,exact_available):
    p=uniform_fixture()
    initial=solve_exact(p,time_limit=10)
    from models.routing.model_6.alns import solver
    def empty(child,**kwargs):
        result=joint_empty_result(child,'alns')
        result['info'].update(status='HEURISTIC_NO_SOLUTION',iterations=0,proven_infeasible=False)
        return result
    monkeypatch.setattr(solver,'solve_alns',empty)
    from models.routing.model_6.alns.batch_search import solve_uniform
    result=solve_uniform(p,solve_candidate=solver.solve_alns,method='alns',budget=.1,max_iterations=2,seed=42,initial_result=initial)
    assert result['audit']['valid'] and result['info']['objective']==initial['info']['objective']
    assert not result['info']['proven_optimal'] and result['info']['best_bound'] is None


def test_alns_budget_is_shared_across_counts_and_without_gurobi():
    code='''
import builtins,sys
original=builtins.__import__
def guarded(name,*args,**kwargs):
    if name.startswith('gurobipy') or '.exact' in name: raise AssertionError(name)
    return original(name,*args,**kwargs)
builtins.__import__=guarded
from tests.routing.test_model6_uniform import uniform_fixture
from models.routing.model_6.alns import solve_alns
r=solve_alns(uniform_fixture(),max_time=.4,max_iterations=10)
assert r['audit']['valid'] and not r['info']['proven_optimal']
assert r['info']['iterations']<=10
assert len(r['count_search'])==2
assert 'gurobipy' not in sys.modules
'''
    subprocess.run([sys.executable,'-c',code],check=True,capture_output=True,text=True)


@pytest.mark.parametrize('options',[
    dict(batch_count_candidates=[]),dict(batch_count_candidates=[0]),dict(batch_count_candidates=[True]),
    dict(batch_count_candidates=[1,1]),dict(batch_count_candidates=[66]),dict(min_phase_minutes=0),
    dict(min_phase_minutes=40,batch_count_candidates=[2]),dict(optimize_cutoffs=True),
])
def test_invalid_uniform_domains_rejected(options):
    source=joint_fixture()
    with pytest.raises(ValueError):
        UniformCycleInstance(source.network,source.orders,source.operating_policy,
                              dict(schedule_mode='uniform_cycle',**options),source.cost_profile.config)


def test_order_slot_restrictions_are_not_silently_removed():
    source=joint_fixture()
    orders=[replace(source.orders[0],allowed_batch_ids=('b1',)),source.orders[1]]
    with pytest.raises(ValueError,match='allow all source batches'):
        UniformCycleInstance(source.network,orders,source.operating_policy,
                              dict(schedule_mode='uniform_cycle'),source.cost_profile.config)


def test_uniform_terminal_cleaning_keeps_equality_but_rejects_later_returns(exact_available):
    source=joint_fixture(zero=True)
    for picked,feasible in [(125,True),(126,False)]:
        orders=[replace(source.orders[0],delivery_window=(0,1),pickup_window=(picked,picked),min_use_time=0)]
        p=UniformCycleInstance(source.network,orders,source.operating_policy,
                              dict(schedule_mode='uniform_cycle',batch_count_candidates=[1,2]),source.cost_profile.config)
        result=solve_exact(p,time_limit=5)
        if feasible:
            assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
            assert result['info']['proven_optimal']
        else:
            assert result['info']['proven_infeasible'] and result['info']['objective'] is None


def test_supplied_k2_seed_remains_k2_when_k1_improves_first(exact_available):
    from models.common.cycle.uniform import attach_uniform_decisions
    p=uniform_fixture()
    initial=solve_exact(p.for_count(2),time_limit=5)
    attach_uniform_decisions(p,initial,2)
    result=solve_alns(p,max_time=.5,max_iterations=20,initial_result=initial)
    assert result['audit']['valid']
    assert result['info']['objective']<=initial['info']['objective']+1e-7


def test_integer_boundaries_avoid_minute_float_roundtrip():
    source=joint_fixture()
    policy=replace(source.operating_policy,time_tick_seconds=1,cycle_end=190*60)
    p=UniformCycleInstance(source.network,source.orders,policy,
                          dict(schedule_mode='uniform_cycle',batch_count_candidates=[3],min_phase_minutes=1),
                          source.cost_profile.config)
    child=p.for_count(3)
    assert child.shared_cutoff_bounds==(60,3740)
    for offset in (60,3740):
        assert child.validated_cutoffs({b.id:b.start+offset for b in child.operating_policy.batches})


def test_cli_uniform_cycle_freezes_inputs_and_reports_actual_k(tmp_path):
    from tools.data.build_model6_demo_suite import build_suite
    from cli.route import main
    manifest=json.loads(build_suite(tmp_path/'data').read_text())
    case=manifest['cases'][0]['m6_case']
    out=tmp_path/'run'
    result=main(['--case',case,'--uniform-cycle','--batch-count-candidates','1','2',
                 '--solver','alns','--time','.5','--iterations','50','--output',str(out)])
    assert result['audit']['valid'] and result['schedule_mode']=='uniform_cycle'
    report=(out/'report.md').read_text()
    assert '选定周期批次数' in report and '统一分界比例' in report
    saved=json.loads((out/'manifest.json').read_text())
    assert saved['uniform_cycle_config']['batch_count_candidates']==[1,2]
    assert saved['legal_order_batches'] is None
    saved=json.loads((out/'result.json').read_text())
    assert saved['cycle_batch_count']==len(saved['batches'])


def test_uniform_comparison_runner_preserves_all_runs_and_uniform_windows(tmp_path, exact_available):
    from tools.data.build_model6_demo_suite import build_suite
    from experiments.runs.run_model6_uniform_comparison import main
    path=build_suite(tmp_path/'data')
    payload=json.loads(path.read_text())
    record=payload['cases'][0]
    config_path=Path(record['m6_case'])
    config=json.loads(config_path.read_text())
    config['joint_batch_options']={'schedule_mode':'uniform_cycle','batch_count_candidates':[1,2]}
    config_path.write_text(json.dumps(config))
    record['total_container_uses']=2
    payload.update(schedule_mode='uniform_cycle',cases=[record])
    path.write_text(json.dumps(payload))
    out=main(['--manifest',str(path),'--output',str(tmp_path/'study'),'--exact-time','3',
              '--time','.4','--iterations','30','--seeds','42','--workers','1'])
    rows=json.loads((out/'rows.json').read_text())
    assert len(rows)==4 and all(r['valid'] for r in rows)
    summary=json.loads((out/'uniform_summary.json').read_text())
    selected=summary['cases'][0]['m6']
    assert selected['cycle_batches'] in (1,2)
    assert selected['first_minutes']+selected['second_minutes']==selected['batch_minutes']
    assert (out/'experiment.json').exists() and (out/'source_snapshot/models/common/cycle/uniform.py').exists()
