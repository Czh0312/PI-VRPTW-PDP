"""Independent validation of the long-budget candidate results and bounds."""
import hashlib
import json
import math
from pathlib import Path
import sys

import pytest

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
PREVIOUS=ROOT/'output/experiments/m8_stability_20261028_v1'
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(PREVIOUS))

from prepare import module
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost
from tools.configuration.loader import load_routing_case
from experiments.runs.research_model8_shared_relaxation import build_relaxation

CASES=['S03','S08']


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def load(case):
    folder=HERE/case
    manifest=read(folder/'manifest.json')
    context=read(Path(manifest['input_context']))
    p=load_instance(*load_routing_case(Path(context['case_path']),use_pool_config=False))
    return p,manifest,context,read(folder/'summary.json'),read(folder/'decomposition_summary.json')


def test_declared_scope_and_same_short_run_information():
    for case,seed in [('S03',31),('S08',19)]:
        _,manifest,context,row,_=load(case)
        assert manifest['seconds']==3600
        assert manifest['seed']==seed
        assert manifest['threads']==1
        short=read(Path(manifest['short_comparison_summary']))
        assert manifest['audited_upper_bound']==short['historical_U']==context['audited_upper_bound']
        assert manifest['common_lower_bound']==short['common_L']==context['common_lower_bound']
        assert row['original_K']==list(range(1,8))
        assert row['seed']==seed
        assert not manifest['mip_start_supplied']
        assert row['audited_upper_bound']<=manifest['audited_upper_bound']+1e-6


@pytest.mark.parametrize('case',CASES)
def test_sources_frozen_algorithm_and_original_clocks(case):
    import zipfile

    p,manifest,context,_,_=load(case)
    for section in ['driver_hashes','source_hashes']:
        for path,digest in manifest[section].items():
            target=Path(path) if Path(path).is_absolute() else ROOT/path
            assert hashlib.sha256(target.read_bytes()).hexdigest()==digest
    for path,digest in context['input_hashes'].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
    old=read(PREVIOUS/'holdout_plan_A.json')['source_hashes']['decomposition_compact.py']
    assert manifest['algorithm_sha256']==old
    dependency=read(HERE/'supplementary_sources.json')
    assert hashlib.sha256((ROOT/dependency['shared_master_path']).read_bytes()).hexdigest()==dependency['shared_master_sha256']
    with zipfile.ZipFile(ROOT/dependency['verified_against_prior_archive']) as archive:
        assert hashlib.sha256(archive.read(dependency['archive_entry'])).hexdigest()==dependency['shared_master_sha256']
    assert context['reduced_N'][0]==context['reduced_N'][1]
    for k in context['reduced_K']:
        policy=p.for_count(k).operating_policy
        assert (policy.time_tick_seconds,policy.time_tick_subdivision)==(
            p.operating_policy.time_tick_seconds,p.operating_policy.time_tick_subdivision)


@pytest.mark.parametrize('case',CASES)
def test_original_business_cost_and_two_cycle_audit(case):
    p,_,_,row,_=load(case)
    result=read(HERE/case/'best_audited_result.json')
    audit=audit_solution(p,result)
    assert audit['valid'],audit.get('errors')
    assert audit['periodic_replay']['valid']
    value=evaluate_comparison_cost(p,result,p.cost_profile.config,audit=audit)['total']
    assert value==pytest.approx(row['audited_upper_bound'],abs=1e-6)
    assert row['global_lower_bound']<=value+1e-6
    if row['global_optimality_certified']:
        assert abs(value-row['global_lower_bound'])<=1e-6
        assert row['algorithm_status']=='GLOBAL_BOUND_CERTIFICATE'
    else:
        assert row['algorithm_status']=='TIME_LIMIT'


@pytest.mark.parametrize('case',CASES)
def test_native_cut_status_arithmetic_and_preserved_trace(case):
    _,manifest,context,row,info=load(case)
    trace=[json.loads(line) for line in (HERE/case/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(trace)==len(info['events'])==row['iterations']
    assert row['native_status']=='MULTIPLE_NATIVE_MASTER_AND_FOLLOWER_SOLVES'
    last=context['common_lower_bound']
    for event,saved in zip(info['events'],trace):
        assert all(saved[key]==value for key,value in event.items())
        assert event['native_status'] in (2,3,9,11)
        assert event['global_lower_bound']>=last-1e-6
        last=event['global_lower_bound']
        assert event['derived_slot_cap']<=event['original_slot_cap']
        if event.get('cut_kind')=='PROVEN_NO_SOLUTION_UNDER_INITIAL_COST_CAP':
            assert event['native_status']==3
        elif event.get('cut_kind')=='VALID_CONDITIONAL_LOWER_BOUND':
            assert event['native_bound'] is not None and math.isfinite(event['native_bound'])
            assert event['gamma']<=manifest['audited_upper_bound']+1e-6
            assert event['bigM']>=event['gamma']-context['common_lower_bound']-1e-6
        else:
            assert event['native_status'] in (9,11)


@pytest.mark.parametrize('case',CASES)
def test_fresh_full_master_reproves_final_global_bound(case):
    import gurobipy as gp

    p,_,context,row,info=load(case)
    inputs=PREVIOUS/'inputs'/case
    allowed=set().union(*[{col['mask'] for col in read(inputs/f'filtered_library_K{k}.json')}
                          for k in context['reduced_K']])
    library=[col for col in read(inputs/'trip_library.json') if col['mask'] in allowed]
    index={col['mask']:i for i,col in enumerate(library)}
    model,ctx=build_relaxation(p,library)
    phase=module(ROOT/'output/experiments/m8_s04_reduction_validation_20261026_v1/phase_bound.py',
                 'long_evidence_phase_'+case)
    try:
        phase.augment(p,model,ctx)
        optimistic=model.getObjective()
        eta=model.addVar(lb=context['common_lower_bound'],ub=row['audited_upper_bound']+1e-7)
        model.addConstr(eta>=optimistic)
        model.setObjective(eta)
        ctx['pool'].LB=ctx['pool'].UB=context['reduced_N'][0]
        for k,variable in ctx['selectors'].items():
            if k not in context['reduced_K']:
                variable.UB=0
        for event in info['events']:
            selected=[ctx['selectors'][event['K']]]
            selected += [ctx['assignments'][i,event['delivery_batches'][o.id]] for i,o in enumerate(p.orders)]
            selected += [ctx['recovery'][i,event['recovery_batches'][o.id]] for i,o in enumerate(p.orders)]
            selected += [ctx['route'][index[mask]] for mask in event['masks']]
            mismatch=gp.quicksum(1-variable for variable in selected)
            if event.get('cut_kind')=='PROVEN_NO_SOLUTION_UNDER_INITIAL_COST_CAP':
                model.addConstr(mismatch>=1)
            elif event.get('cut_kind')=='VALID_CONDITIONAL_LOWER_BOUND':
                model.addConstr(eta>=event['gamma']-1e-7-event['bigM']*mismatch)
        model.Params.Threads=1
        model.Params.Seed=42
        model.Params.TimeLimit=300
        model.Params.BestBdStop=row['global_lower_bound']-1e-8
        model.optimize()
        assert model.Status not in (gp.GRB.INFEASIBLE,gp.GRB.NUMERIC)
        assert model.ObjBound>=row['global_lower_bound']-1e-6
        assert model.ObjBound<=row['audited_upper_bound']+1e-5
        if row['global_optimality_certified']:
            assert abs(row['audited_upper_bound']-model.ObjBound)<=1e-6
        proof=dict(case=case,native_status=model.Status,independent_global_bound=model.ObjBound,
            target_bound=row['global_lower_bound'],audited_upper_bound=row['audited_upper_bound'],
            runtime=model.Runtime,seed=42,threads=1,cuts_rebuilt=info['cuts'])
        (HERE/case/'independent_bound_replay.json').write_text(
            json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        model.dispose()
