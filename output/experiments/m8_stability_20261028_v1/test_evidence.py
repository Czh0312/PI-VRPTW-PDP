"""Independent business replay and full-master cost-certificate reconstruction."""
import hashlib
import json
from pathlib import Path
import re
import sys

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0,str(HERE))
from prepare import module
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost
from tools.configuration.loader import load_routing_case
from experiments.runs.research_model8_shared_relaxation import build_relaxation

TRIALS = sorted(path.parent for path in (HERE/'runs').rglob('summary.json'))
DECOMPOSED = [folder for folder in TRIALS if (folder/'decomposition_summary.json').exists()]


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def load_problem(folder):
    manifest = read(folder/'manifest.json')
    context = read(Path(manifest['input_context']))
    p = load_instance(*load_routing_case(Path(context['case_path']),use_pool_config=False))
    return p,manifest,context


def test_all_predeclared_holdouts_are_present_and_information_is_matched():
    holds = [read(path/'summary.json') for path in TRIALS if read(path/'summary.json')['phase']=='holdout']
    assert len(holds)==18
    for case in ['S03','S08','S05']:
        for seed in [7,19,31]:
            pair = [row for row in holds if row['case']==case and row['seed']==seed]
            assert {row['profile'] for row in pair}=={'previous','compact_decomposition'}
            assert len(pair)==2
            assert pair[0]['historical_U']==pair[1]['historical_U']
            assert pair[0]['common_L']==pair[1]['common_L']
            for row in pair:
                assert row['original_K']==list(range(1,8))
    for worker in ['A','B']:
        plan = read(HERE/f'holdout_plan_{worker}.json')
        assert plan['held_out_seeds']==[7,19,31]
        for name,digest in plan['source_hashes'].items():
            assert hashlib.sha256((HERE/name).read_bytes()).hexdigest()==digest


@pytest.mark.parametrize('folder',TRIALS,ids=lambda path:str(path.relative_to(HERE/'runs')))
def test_trial_frozen_sources_and_input_proofs(folder):
    _,manifest,context = load_problem(folder)
    for key in ['driver_hashes','source_hashes']:
        for path,digest in manifest[key].items():
            target = Path(path) if Path(path).is_absolute() else ROOT/path
            assert hashlib.sha256(target.read_bytes()).hexdigest()==digest
    for path,digest in context['input_hashes'].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
    assert manifest['threads']==1


@pytest.mark.parametrize('folder',TRIALS,ids=lambda path:str(path.relative_to(HERE/'runs')))
def test_final_plan_original_cost_and_two_continuous_cycles(folder):
    p,_,_ = load_problem(folder)
    row = read(folder/'summary.json')
    result = read(folder/'best_audited_result.json')
    audit = audit_solution(p,result)
    assert audit['valid'],audit.get('errors')
    assert audit['periodic_replay']['valid']
    cost = evaluate_comparison_cost(p,result,p.cost_profile.config,audit=audit)
    assert cost['total']==pytest.approx(row['audited_upper_bound'],abs=1e-6)
    assert row['global_lower_bound']<=cost['total']+1e-6
    if row['global_optimality_certified']:
        assert row['global_lower_bound']==pytest.approx(cost['total'],abs=1e-6)


@pytest.mark.parametrize('folder',TRIALS,ids=lambda path:str(path.relative_to(HERE/'runs')))
def test_native_status_and_certificate_not_confused(folder):
    row = read(folder/'summary.json')
    if (folder/'native_summary.json').exists():
        native = read(folder/'native_summary.json')
        assert native['status']==row['native_status']
        if row['global_optimality_certified'] and native['status']!='OPTIMAL':
            assert native['certificate_callback']
            closure = native['certificate_callback'][0]
            assert closure['valid_global_bound']==pytest.approx(closure['audited_feasible_cost'],abs=1e-6)
        if row['profile']=='warm_focus':
            assert native['mip_start_supplied']
            assert 'Loaded user MIP start with objective' in (folder/'gurobi.log').read_text(encoding='utf-8')
    else:
        data = read(folder/'decomposition_summary.json')
        assert row['native_status']=='MULTIPLE_NATIVE_MASTER_AND_FOLLOWER_SOLVES'
        assert data['global_optimality_certified']==row['global_optimality_certified']
        for event in data['events']:
            assert event['native_status'] in (2,3,9,11)
            if event.get('cut_kind')=='PROVEN_NO_SOLUTION_UNDER_INITIAL_COST_CAP':
                assert event['native_status']==3
            elif event.get('cut_kind')=='VALID_CONDITIONAL_LOWER_BOUND':
                assert event['native_bound'] is not None
                assert event['bigM']>=event['gamma']-row['common_L']-1e-6
            if 'derived_slot_cap' in event:
                assert event['derived_slot_cap']<=event['original_slot_cap']


@pytest.mark.parametrize('case',['S03','S08','S05'])
def test_baseline_model_fingerprint_is_identical_across_heldout_seeds(case):
    paths = [HERE/'runs/holdout'/case/'previous'/f's{seed}'/'gurobi.log' for seed in [7,19,31]]
    fingerprints = [re.search(r'Model fingerprint: (\S+)',path.read_text(encoding='utf-8')).group(1) for path in paths]
    assert len(set(fingerprints))==1


@pytest.mark.parametrize('case',['S03','S08','S05'])
def test_candidate_retained_K_uses_identical_physical_clock_units(case):
    context = read(HERE/'inputs'/case/'context.json')
    p = load_instance(*load_routing_case(Path(context['case_path']),use_pool_config=False))
    parent = p.operating_policy
    for k in context['reduced_K']:
        child = p.for_count(k).operating_policy
        assert child.time_tick_seconds==parent.time_tick_seconds
        assert child.time_tick_subdivision==parent.time_tick_subdivision
        assert child.cycle_start==parent.cycle_start and child.cycle_end==parent.cycle_end


@pytest.mark.parametrize('case',['S03','S08','S05'])
def test_rederive_original_all_K_cost_competitive_domain(case):
    folder = HERE/'inputs'/case
    context = read(folder/'context.json')
    p = load_instance(*load_routing_case(Path(context['case_path']),use_pool_config=False))
    helper = module(ROOT/'output/experiments/m8_remaining_small_optimality_20261028_v1/run_case.py',
                    'fresh_domain_replay_'+case)
    replay = HERE/'validation_domain_replay'/case
    replay.mkdir(parents=True,exist_ok=False)
    actual = helper.derive_reduction(p,read(Path(context['witness_path'])),replay)
    expected = read(folder/'reduction_certificate.json')
    for key in ['original_K','retained_K','K_min','K_max','N_min','N_max']:
        assert actual[key]==expected[key]
    assert actual['audited_upper_bound']==pytest.approx(context['audited_upper_bound'],abs=1e-6)


@pytest.mark.parametrize('case',['S03','S08','S05'])
def test_recompute_every_cost_screened_trip_mask(case):
    from cost_screening import screen_route_library

    folder = HERE/'inputs'/case
    context = read(folder/'context.json')
    p = load_instance(*load_routing_case(Path(context['case_path']),use_pool_config=False))
    phase = module(ROOT/'output/experiments/m8_s04_reduction_validation_20261026_v1/phase_bound.py',
                   'fresh_screen_phase_'+case)
    patterns = phase.phase_patterns(p)
    for k in context['reduced_K']:
        kept,certificate = screen_route_library(p.for_count(k),read(folder/'trip_library.json'),
            context['audited_upper_bound'],context['reduced_N'][0],patterns)
        expected = read(folder/f'screening_K{k}.json')
        assert [row['mask'] for row in kept]==[row['mask'] for row in read(folder/f'filtered_library_K{k}.json')]
        assert certificate['retained_columns']==expected['retained_columns']
        for actual,wanted in zip(certificate['rows'],expected['rows']):
            assert actual['mask']==wanted['mask']
            assert actual['kept']==wanted['kept']
            if wanted['conditional_cost_lower_bound'] is not None:
                assert actual['conditional_cost_lower_bound']==pytest.approx(wanted['conditional_cost_lower_bound'],abs=1e-6)


@pytest.mark.parametrize('folder',DECOMPOSED,ids=lambda path:str(path.relative_to(HERE/'runs')))
def test_rebuild_entire_refined_master_and_reprove_reported_global_bound(folder):
    import gurobipy as gp

    p,_,context = load_problem(folder)
    row = read(folder/'summary.json')
    data = read(folder/'decomposition_summary.json')
    inputs = HERE/'inputs'/row['case']
    original = read(inputs/'trip_library.json')
    allowed = set().union(*[{col['mask'] for col in read(inputs/f'filtered_library_K{k}.json')}
                            for k in context['reduced_K']])
    library = [col for col in original if col['mask'] in allowed]
    index = {col['mask']:i for i,col in enumerate(library)}
    model,ctx = build_relaxation(p,library)
    phase = module(ROOT/'output/experiments/m8_s04_reduction_validation_20261026_v1/phase_bound.py','replay_phase')
    try:
        phase.augment(p,model,ctx)
        original_cost = model.getObjective()
        eta = model.addVar(lb=context['common_lower_bound'],ub=row['audited_upper_bound']+1e-7)
        model.addConstr(eta>=original_cost)
        model.setObjective(eta)
        ctx['pool'].LB=ctx['pool'].UB=context['reduced_N'][0]
        for k,selector in ctx['selectors'].items():
            if k not in context['reduced_K']:
                selector.UB=0
        for event in data['events']:
            chosen = [ctx['selectors'][event['K']]]
            chosen += [ctx['assignments'][i,event['delivery_batches'][o.id]] for i,o in enumerate(p.orders)]
            chosen += [ctx['recovery'][i,event['recovery_batches'][o.id]] for i,o in enumerate(p.orders)]
            chosen += [ctx['route'][index[mask]] for mask in event['masks']]
            mismatch = gp.quicksum(1-variable for variable in chosen)
            if event.get('cut_kind')=='PROVEN_NO_SOLUTION_UNDER_INITIAL_COST_CAP':
                model.addConstr(mismatch>=1)
            elif event.get('cut_kind')=='VALID_CONDITIONAL_LOWER_BOUND':
                model.addConstr(eta>=event['gamma']-1e-7-event['bigM']*mismatch)
        model.Params.TimeLimit=60
        model.Params.Seed=row['seed']
        model.Params.BestBdStop=row['global_lower_bound']-1e-8
        model.optimize()
        assert model.Status not in (gp.GRB.INFEASIBLE,gp.GRB.NUMERIC)
        assert model.ObjBound>=row['global_lower_bound']-1e-6
        assert model.ObjBound<=row['audited_upper_bound']+1e-5
    finally:
        model.dispose()


def test_reprove_control_certificate_patterns_with_original_untrimmed_slots():
    """Independently remove the experimental slot trimming on every S05 cut."""
    import gurobipy as gp
    from models.routing.model_8.exact.formulation import build_model
    from route_bounds import add_shared_route_bounds

    context = read(HERE/'inputs/S05/context.json')
    p = load_instance(*load_routing_case(Path(context['case_path']),use_pool_config=False))
    library = read(HERE/'inputs/S05/trip_library.json')
    unique = {}
    for folder in DECOMPOSED:
        if read(folder/'summary.json')['case']!='S05':
            continue
        for event in read(folder/'decomposition_summary.json')['events']:
            key = (event['K'],tuple(sorted(event['delivery_batches'].items())),
                   tuple(sorted(event['recovery_batches'].items())),tuple(sorted(event['masks'])))
            previous = unique.get(key)
            if previous is None or event.get('cut_kind')=='PROVEN_NO_SOLUTION_UNDER_INITIAL_COST_CAP':
                unique[key]=event
            elif previous.get('cut_kind')!='PROVEN_NO_SOLUTION_UNDER_INITIAL_COST_CAP' and event.get('gamma',0)>previous.get('gamma',0):
                unique[key]=event
    assert unique
    replay = []
    for key,event in unique.items():
        child = p.for_count(event['K'])
        model,sub = build_model(child,compact_e2=True,strengthening='physical_time')
        try:
            assert child.operating_policy.e2_max_trips_per_vehicle==p.operating_policy.e2_max_trips_per_vehicle
            model.Params.OutputFlag=0
            model.Params.Threads=1
            model.Params.Seed=42
            model.Params.TimeLimit=30
            model.Params.MIPGap=0
            model.Params.MIPGapAbs=0
            sub['pool'].LB=sub['pool'].UB=context['reduced_N'][0]
            batches = [b.id for b in child.operating_policy.batches]
            for (oid,bid),variable in sub['assignments'].items():
                variable.LB=variable.UB=int(bid==batches[event['delivery_batches'][oid]])
            model.update()
            for oid,b in event['recovery_batches'].items():
                for i,bid in enumerate(batches):
                    variable=model.getVarByName(f'eligible_{oid}_{bid}')
                    assert variable is not None
                    variable.LB=variable.UB=int(i>=b)
            add_shared_route_bounds(child,model,sub,library)
            for i,row in enumerate(library):
                variable=model.getVarByName(f'bound_shared_mask[{i}]')
                variable.LB=variable.UB=int(row['mask'] in event['masks'])
            model.addConstr(model.getObjective()<=context['audited_upper_bound']+1e-7)
            model.addConstr(model.getObjective()>=context['common_lower_bound']-1e-7)
            no_good=event.get('cut_kind')=='PROVEN_NO_SOLUTION_UNDER_INITIAL_COST_CAP'
            if not no_good:
                assert event.get('cut_kind')=='VALID_CONDITIONAL_LOWER_BOUND'
                model.Params.BestBdStop=event['gamma']-1e-8
            model.optimize()
            if no_good:
                assert model.Status==gp.GRB.INFEASIBLE,(key,model.Status)
            else:
                assert model.ObjBound>=event['gamma']-1e-6,(key,model.Status,model.ObjBound,event['gamma'])
            replay.append(dict(K=event['K'],delivery_batches=event['delivery_batches'],
                recovery_batches=event['recovery_batches'],masks=event['masks'],
                source_cut_kind=event['cut_kind'],native_status=model.Status,runtime=model.Runtime,
                original_slot_cap=child.operating_policy.e2_max_trips_per_vehicle,
                native_bound=None if no_good else model.ObjBound))
        finally:
            model.dispose()
    (HERE/'original_slot_replay.json').write_text(json.dumps(dict(case='S05',
        independently_reproved_unique_patterns=len(unique),patterns=replay),ensure_ascii=False,indent=2),encoding='utf-8')
