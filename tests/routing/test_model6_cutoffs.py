"""Variable cutoff, phase overlap, cleaning and independently replayed incumbents."""
from copy import deepcopy
from dataclasses import replace
import itertools
import json

import pytest

from models.routing.common.cycle.joint import JointBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.variants.model_6 import solve_exact, solve_alns
from tools.data.build_model6_demo_suite import synthetic_profile
from tests.routing.test_model6_joint import joint_fixture, exact_available


def flexible(base, bounds=None, **options):
    choices={**base.options,'optimize_cutoffs':True,**options}
    if bounds is not None:
        choices['cutoff_bounds_minutes']=bounds
    return JointBatchInstance(base.network,base.orders,base.operating_policy,
                              choices,base.cost_profile.config)


def test_variable_cutoffs_restore_clean_stock_for_next_batch(exact_available):
    base=joint_fixture(pool_rate=30,cleaning=25,pool_max=1,
                       fixed_assignment={'o1':'b1','o2':'b2'})
    assert solve_exact(base,time_limit=10)['info']['proven_infeasible']
    p=flexible(base,{'b1':[20,50],'b2':[80,110]})
    r=solve_exact(p,time_limit=10)
    assert r['info']['proven_optimal'] and r['audit']['valid']
    assert r['container_pool_size']==1 and r['delivery_batch_count']==2
    assert r['batch_cutoffs']['b1']<=30
    assert r['audit']['clean_stock'][1]['available']==1
    assert r['audit']['periodic_replay']['valid']
    h=solve_alns(p,max_time=1,max_iterations=1000,seed=42)
    assert h['audit']['valid'] and h['container_pool_size']==1
    assert h['info']['operator_attempts']['shift_cutoff']>0
    assert h['info']['operator_attempts']['align_cutoff']>0


def test_cutoff_equality_and_one_tick_late_change_recovery_round(exact_available):
    base=joint_fixture(pool_rate=30,fixed_assignment={'o1':'b1','o2':'b2'},pool_max=1)
    base.orders=tuple([replace(base.orders[0],pickup_window=(45,45)),base.orders[1]])
    p=flexible(base,{'b1':[45,46],'b2':[90,100]})
    r=solve_exact(p,time_limit=10)
    assert r['info']['proven_optimal'] and r['audit']['valid']
    assert r['batch_cutoffs']['b1']==46
    assert any(t['return_complete']==46 for t in r['e2_trips']
               if any(e['order_id']=='o1' and e['kind']=='p' for e in t['events']))
    assert [s['quantity'] for s in r['recovery_rounds']]==[1,1]
    earlier=flexible(base,{'b1':[45,45],'b2':[90,100]})
    assert solve_exact(earlier,time_limit=10)['info']['proven_infeasible']


def test_e2_can_recover_during_second_phase_and_wait_for_next_round(exact_available):
    base=joint_fixture()
    orders=[replace(base.orders[0],delivery_window=(0,20),pickup_window=(45,45),allowed_batch_ids=('b1',))]
    p=JointBatchInstance(base.network,orders,base.operating_policy,
        dict(optimize_cutoffs=True,cutoff_bounds_minutes={'b1':[30,40],'b2':[80,100]}),synthetic_profile())
    r=solve_exact(p,time_limit=10)
    assert r['info']['proven_optimal'] and r['audit']['valid']
    trip=next(t for t in r['e2_trips'] if any(e['kind']=='p' for e in t['events']))
    assert r['batch_cutoffs']['b1']<trip['return_complete']<p.batch_map['b1'].end
    assert [s['quantity'] for s in r['recovery_rounds']]==[0,1]
    assert not r['delivery_batch_activation']['b2']
    assert any(t['batch_id']=='b2' and all(e['kind']=='p' for e in t['events']) for t in r['e1_trips'])


def test_exact_variable_optimum_matches_exhaustive_fixed_cutoffs(exact_available):
    base=joint_fixture(pool_rate=30)
    p=flexible(base,{'b1':[39,41],'b2':[99,100]})
    joint=solve_exact(p,time_limit=10)
    assert joint['info']['proven_optimal'] and joint['audit']['valid']
    costs=[]
    for first,last in itertools.product(range(39,42),range(99,101)):
        policy=replace(base.operating_policy,batches=tuple(
            replace(b,cutoff=c) for b,c in zip(base.operating_policy.batches,(first,last))))
        fixed=JointBatchInstance(base.network,base.orders,policy,base.options,base.cost_profile.config)
        r=solve_exact(fixed,time_limit=10)
        assert r['info']['proven_optimal'] and r['audit']['valid']
        costs.append(r['info']['objective'])
    assert joint['info']['objective']==pytest.approx(min(costs))


def test_legal_domain_uses_allowed_cutoff_upper_bound(exact_available):
    base=joint_fixture()
    policy=replace(base.operating_policy,batches=(replace(base.operating_policy.batches[0],cutoff=4),base.operating_policy.batches[1]))
    fixed=JointBatchInstance(base.network,base.orders,policy,{'fixed_assignment':{'o1':'b1','o2':'b2'}},base.cost_profile.config)
    assert not fixed.legal_batches['o1']
    p=flexible(fixed,{'b1':[4,20],'b2':[90,110]})
    assert p.legal_batches['o1']==('b1',)
    r=solve_exact(p,time_limit=10)
    assert r['audit']['valid'] and r['batch_cutoffs']['b1']>=5


@pytest.mark.parametrize('mutate',[
    lambda r:r['batch_cutoffs'].update(b1=59),
    lambda r:r.pop('batch_cutoffs'),
    lambda r:r['batches'][0].update(cutoff=1),
    lambda r:r['operating_policy']['batches'][0].update(cutoff=1),
    lambda r:r['cutoff_bounds']['b1'].__setitem__(0,0),
    lambda r:r.update(cutoff_mode='fixed'),
])
def test_saved_cutoffs_cannot_be_forged(mutate):
    p=flexible(joint_fixture(),{'b1':[20,50],'b2':[80,110]})
    result=json.loads(json.dumps(solve_alns(p,max_time=.2,max_iterations=100)))
    assert audit_solution(p,result)['valid']
    mutate(result)
    assert not audit_solution(p,result)['valid']


@pytest.mark.parametrize('options',[
    dict(optimize_cutoffs=1),
    dict(cutoff_bounds_minutes={'b1':[20,50],'b2':[80,110]}),
    dict(optimize_cutoffs=True,cutoff_bounds_minutes={'b1':[20,50]}),
    dict(optimize_cutoffs=True,cutoff_bounds_minutes={'b1':[0,50],'b2':[80,110]}),
    dict(optimize_cutoffs=True,cutoff_bounds_minutes={'b1':[20,60],'b2':[80,110]}),
    dict(optimize_cutoffs=True,cutoff_bounds_minutes={'b1':[50,20],'b2':[80,110]}),
])
def test_invalid_cutoff_domains_rejected(options):
    base=joint_fixture()
    with pytest.raises(ValueError):
        JointBatchInstance(base.network,base.orders,base.operating_policy,options,base.cost_profile.config)


def test_alns_retains_audited_fixed_window_incumbent_without_claiming_proof(exact_available):
    base=joint_fixture(pool_rate=30)
    initial=solve_exact(base,time_limit=10)
    p=flexible(base,{'b1':[20,50],'b2':[80,110]})
    h=solve_alns(p,max_time=.1,max_iterations=0,initial_result=initial)
    assert h['audit']['valid'] and not h['info']['proven_optimal']
    assert h['info']['initial_incumbent_cost']==pytest.approx(initial['info']['objective'])
    assert h['info']['objective']<=initial['info']['objective']+1e-7
    wrong=deepcopy(initial); wrong['e1_trips'][0]['events'][0]['quantity']=999
    with pytest.raises(ValueError,match='physical'):
        solve_alns(p,max_time=.1,initial_result=wrong)


def test_evaluator_cache_separates_identical_routes_with_different_cutoffs():
    from models.routing.variants.model_6.alns.solver import JointEvaluator,clone_state
    from models.routing.solvers.alns.evaluator import initial_state
    import random
    p=flexible(joint_fixture(pool_rate=30),{'b1':[35,45],'b2':[95,105]})
    assignment={'o1':'b1','o2':'b2'}; evaluator=JointEvaluator(p)
    state=initial_state(evaluator.fixed(assignment),random.Random(42))
    state['cutoffs']={'b1':40,'b2':100}
    first=evaluator(assignment,state)
    moved=clone_state(state); moved['cutoffs']['b1']=35
    second=evaluator(assignment,moved)
    assert first[1]['audit']['valid'] and second[1]['audit']['valid']
    assert first[0]!=second[0] and evaluator.misses==2
    assert evaluator(assignment,state)[0]==first[0] and evaluator.hits==1


def test_cli_variable_cutoffs_and_frozen_initial_solution(tmp_path,exact_available):
    from tools.data.build_model6_demo_suite import build_suite
    from tools.configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from cli.route import main
    manifest=build_suite(tmp_path/'data')
    case=json.loads(manifest.read_text())['cases'][0]['m6_case']
    p=load_instance(*load_routing_case(case,use_pool_config=False))
    initial=solve_exact(p,time_limit=10)
    path=tmp_path/'initial.json'; path.write_text(json.dumps(initial))
    r=main(['--case',case,'--solver','alns','--optimize-cutoffs','--time','.1',
            '--iterations','0','--initial-joint-result',str(path),'--output',str(tmp_path/'run')])
    assert r['cutoff_mode']=='optimized' and r['audit']['valid']
    assert r['info']['objective']<=initial['info']['objective']+1e-7
    assert any('initial.json' in p.name for p in (tmp_path/'run/inputs').iterdir())
    assert '第一窗/分钟' in (tmp_path/'run/report.md').read_text()


def test_cutoff_comparison_runner_records_both_domains_and_failures(tmp_path,exact_available):
    from tools.data.build_model6_demo_suite import build_suite
    from experiments.runs.run_model6_cutoff_comparison import main
    manifest=build_suite(tmp_path/'data')
    payload=json.loads(manifest.read_text()); payload['cases']=payload['cases'][:1]
    manifest.write_text(json.dumps(payload))
    case_path=payload['cases'][0]['m6_case']; config=json.loads(open(case_path).read())
    config['joint_batch_options']['optimize_cutoffs']=True
    open(case_path,'w').write(json.dumps(config))
    out=main(['--manifest',str(manifest),'--time','.15','--exact-time','5','--iterations','100',
              '--seeds','42','--workers','2','--output',str(tmp_path/'comparison')])
    rows=json.loads((out/'rows.json').read_text())
    assert len(rows)==6 and all(r['valid'] for r in rows)
    assert {r['treatment'] for r in rows}=={'M5_distance','M6_fixed_windows','M6_joint'}
    summary=json.loads((out/'cutoff_summary.json').read_text())
    assert summary['statistics']['variable_cost_higher']==0
    assert summary['cases'][0]['optimized']['cost_optimal_proven']
    assert (out/'source_snapshot/models/routing/variants/model_6/exact/model.py').exists()
