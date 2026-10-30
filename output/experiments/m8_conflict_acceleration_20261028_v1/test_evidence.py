"""Matched new-seed checks, fresh master proofs and original-slot follower replay."""
import hashlib
import json
import math
from pathlib import Path

import gurobipy as gp
import pytest

from common import HERE,ROOT,OLD,load,read,save,sha,module,project
from master_sync import augment,violations
from models.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost
from experiments.runs.research_model8_shared_relaxation import build_relaxation

TRIALS=sorted(path.parent for path in (HERE/'runs').rglob('summary.json'))
CANDIDATES=[folder for folder in TRIALS if read(folder/'summary.json')['profile']=='sync']


def test_complete_predeclared_new_seed_pairs_and_unchanged_inputs():
    rows=[read(folder/'summary.json') for folder in TRIALS if read(folder/'summary.json')['phase']=='confirmatory']
    assert len(rows)==18
    for case in ['S03','S08','S05']:
        for seed in [11,23,37]:
            pair=[row for row in rows if (row['case'],row['seed'])==(case,seed)]
            assert len(pair)==2 and {row['profile'] for row in pair}=={'baseline','sync'}
            assert pair[0]['historical_U']==pair[1]['historical_U']
            assert pair[0]['common_L']==pair[1]['common_L']
            assert all(row['original_K']==list(range(1,8)) for row in pair)
    for worker in ['A','B']:
        plan=read(HERE/f'confirmatory_plan_{worker}.json')
        assert plan['seeds']==[11,23,37]
        for path,digest in plan['source_hashes'].items():
            assert sha(path)==digest


@pytest.mark.parametrize('folder',TRIALS,ids=lambda folder:str(folder.relative_to(HERE/'runs')))
def test_frozen_sources_case_data_and_original_plan_audit(folder):
    manifest=read(folder/'manifest.json')
    row=read(folder/'summary.json')
    p,_,context,_,_=load(row['case'])
    for category in ['driver_hashes','source_hashes']:
        for path,digest in manifest[category].items():
            target=Path(path) if Path(path).is_absolute() else ROOT/path
            assert sha(target)==digest
    assert not manifest['newly_proven_optimum_used_as_lower_bound']
    assert manifest['common_L']==context['common_lower_bound']
    assert manifest['threads']==1
    result=read(folder/'best_audited_result.json')
    audit=audit_solution(p,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    cost=evaluate_comparison_cost(p,result,p.cost_profile.config,audit=audit)['total']
    assert cost==pytest.approx(row['audited_upper_bound'],abs=1e-6)
    assert row['global_lower_bound']<=cost+1e-6
    if row['global_optimality_certified']:
        assert abs(cost-row['global_lower_bound'])<=1e-6
    if row['profile']=='sync':
        assert not violations(p,project(p,result))


@pytest.mark.parametrize('folder',CANDIDATES,ids=lambda folder:str(folder.relative_to(HERE/'runs')))
def test_fresh_strengthened_master_without_optimum_floor_reproves_bound(folder):
    row=read(folder/'summary.json')
    info=read(folder/'algorithm_summary.json')
    p,_,context,library,phase=load(row['case'])
    model,ctx=build_relaxation(p,library)
    index={col['mask']:i for i,col in enumerate(library)}
    try:
        phase.augment(p,model,ctx)
        augment(p,model,ctx,library)
        original=model.getObjective()
        # Deliberately no upper bound on eta; the newly known optimum is not
        # provided as a floor or an objective equality in this independent proof.
        eta=model.addVar(lb=context['common_lower_bound'])
        model.addConstr(eta>=original)
        model.setObjective(eta)
        ctx['pool'].LB=ctx['pool'].UB=context['reduced_N'][0]
        for k,selector in ctx['selectors'].items():
            if k not in context['reduced_K']:
                selector.UB=0
        for event in info['events']:
            selected=[ctx['selectors'][event['K']]]
            selected += [ctx['assignments'][i,event['delivery_batches'][o.id]] for i,o in enumerate(p.orders)]
            selected += [ctx['recovery'][i,event['recovery_batches'][o.id]] for i,o in enumerate(p.orders)]
            selected += [ctx['route'][index[mask]] for mask in event['masks']]
            mismatch=gp.quicksum(1-variable for variable in selected)
            assert event['native_status'] in (2,3,9,11)
            if event.get('cut_kind')=='PROVEN_NO_SOLUTION_UNDER_INITIAL_COST_CAP':
                assert event['native_status']==gp.GRB.INFEASIBLE
                model.addConstr(mismatch>=1)
            elif event.get('cut_kind')=='VALID_CONDITIONAL_LOWER_BOUND':
                assert event['native_bound'] is not None and math.isfinite(event['native_bound'])
                assert event['bigM']>=event['gamma']-context['common_lower_bound']-1e-6
                model.addConstr(eta>=event['gamma']-1e-7-event['bigM']*mismatch)
        model.Params.Seed=101
        model.Params.Threads=1
        model.Params.TimeLimit=60
        model.optimize()
        assert model.Status==gp.GRB.OPTIMAL
        assert model.ObjBound>=row['global_lower_bound']-1e-6
        assert model.ObjBound<=row['audited_upper_bound']+1e-6
        if row['global_optimality_certified']:
            assert abs(row['audited_upper_bound']-model.ObjBound)<=1e-6
        save(folder/'independent_master_replay.json',dict(native_status=model.Status,
            independently_proven_lower_bound=model.ObjBound,audited_upper_bound=row['audited_upper_bound'],
            seconds=model.Runtime,seed=101,known_optimum_supplied_as_floor=False,cuts_rebuilt=info['cuts']))
    finally:
        model.dispose()


def test_all_distinct_new_follower_cuts_hold_with_original_untrimmed_slots():
    from models.routing.model_8.exact.formulation import build_model
    from route_bounds import add_shared_route_bounds

    unique={}
    for folder in CANDIDATES:
        row=read(folder/'summary.json')
        for event in read(folder/'algorithm_summary.json')['events']:
            key=(row['case'],event['K'],tuple(sorted(event['delivery_batches'].items())),
                 tuple(sorted(event['recovery_batches'].items())),tuple(event['masks']))
            previous=unique.get(key)
            if previous is None or event.get('cut_kind')=='PROVEN_NO_SOLUTION_UNDER_INITIAL_COST_CAP':
                unique[key]=event
    replays=[]
    for key,event in unique.items():
        case=key[0]
        p,_,context,library,_=load(case)
        child=p.for_count(event['K'])
        model,sub=build_model(child,compact_e2=True,strengthening='physical_time')
        try:
            assert child.operating_policy.e2_max_trips_per_vehicle==p.operating_policy.e2_max_trips_per_vehicle
            model.Params.OutputFlag=0
            model.Params.Threads=1
            model.Params.Seed=101
            model.Params.TimeLimit=30
            sub['pool'].LB=sub['pool'].UB=context['reduced_N'][0]
            batches=[b.id for b in child.operating_policy.batches]
            for (oid,bid),variable in sub['assignments'].items():
                variable.LB=variable.UB=int(bid==batches[event['delivery_batches'][oid]])
            model.update()
            for oid,b in event['recovery_batches'].items():
                for i,bid in enumerate(batches):
                    variable=model.getVarByName(f'eligible_{oid}_{bid}')
                    variable.LB=variable.UB=int(i>=b)
            add_shared_route_bounds(child,model,sub,library)
            for i,col in enumerate(library):
                variable=model.getVarByName(f'bound_shared_mask[{i}]')
                variable.LB=variable.UB=int(col['mask'] in event['masks'])
            model.addConstr(model.getObjective()<=context['audited_upper_bound']+1e-7)
            model.addConstr(model.getObjective()>=context['common_lower_bound']-1e-7)
            model.optimize()
            if event.get('cut_kind')=='PROVEN_NO_SOLUTION_UNDER_INITIAL_COST_CAP':
                assert model.Status==gp.GRB.INFEASIBLE
            else:
                assert model.ObjBound>=event['gamma']-1e-6
            replays.append(dict(case=case,K=event['K'],masks=event['masks'],native_status=model.Status,
                original_slot_cap=child.operating_policy.e2_max_trips_per_vehicle,seconds=model.Runtime))
        finally:
            model.dispose()
    save(HERE/'original_slot_cut_replay.json',dict(unique_pattern_count=len(unique),replays=replays))


@pytest.mark.parametrize('case',['S03','S08','S05'])
def test_input_only_master_with_all_K_full_library_and_no_historical_bounds(case):
    from experiments.runs.research_model8_shared_relaxation import enumerate_trip_library

    p,witness,_,_,phase=load(case)
    full_library,_=enumerate_trip_library(p)
    model,ctx=build_relaxation(p,full_library)
    try:
        phase.augment(p,model,ctx)
        augment(p,model,ctx,full_library)
        # Do not fix K or pool, do not prune columns by a feasible U, and do
        # not supply an incumbent cost or any historical objective lower bound.
        model.Params.Seed=101
        model.Params.Threads=1
        model.Params.TimeLimit=60
        model.optimize()
        assert model.Status==gp.GRB.OPTIMAL
        upper=audit_solution(p,witness)['objective']
        assert model.ObjBound<=upper+1e-6
        save(HERE/f'input_only_bound_{case}.json',dict(case=case,original_K=list(p.batch_count_candidates),
            full_route_columns=len(full_library),native_status=model.Status,lower_bound=model.ObjBound,
            native_runtime=model.Runtime,seed=101,prior_U_or_L_supplied=False,
            K_or_pool_fixed=False,audited_cost_used_only_for_postsolve_check=upper))
    finally:
        model.dispose()
