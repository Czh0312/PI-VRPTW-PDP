"""Matched proof-stage M8 stability trial; all profiles get identical U/L.

The same observational callback can end either arm once a finite Gurobi
global bound matches the independently audited supplied feasible cost. The
native INTERRUPTED status remains intact; this is an external certificate.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0,str(ROOT))
from route_bounds import add_shared_route_bounds
from warm_start import apply_audited_start
from experiments.runs.probe_model8_physical_bounds import source_hashes
from experiments.runs.research_model8_shared_relaxation import enumerate_trip_library
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import attach_calendar_decisions
from models.common.cycle.unified_milp import build_unified_model
from models.common.solver_config import read_milp_info, accept_milp_audit
from models.routing.model_8.exact.formulation import build_model
from models.routing.model_8.exact.solution import extract_solution
from tools.configuration.loader import load_routing_case


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path,value):
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finite(value):
    return float(value) if math.isfinite(value) and abs(value)<1e90 else None


def run(args):
    import gurobipy as gp

    begun = time.perf_counter()
    out = HERE/'runs'/args.phase/args.case/args.profile/f's{args.seed}'
    out.mkdir(parents=True,exist_ok=False)
    context_path = HERE/'inputs'/args.case/'context.json'
    context = read(context_path)
    p = load_instance(*load_routing_case(Path(context['case_path']),use_pool_config=False))
    witness = read(Path(context['witness_path']))
    witness_audit = audit_solution(p,witness)
    assert witness_audit['valid'] and witness_audit['periodic_replay']['valid']
    upper = witness_audit['objective']
    lower = context['common_lower_bound']
    counts = context['previous_K'] if args.profile=='previous' else context['reduced_K']
    pool_bounds = context['previous_N'] if args.profile=='previous' else context['reduced_N']
    files = [Path(__file__),HERE/'route_bounds.py',HERE/'warm_start.py',context_path,HERE/'inputs'/args.case/'trip_library.json']
    hashes = {str(path):sha(path) for path in files}
    source = source_hashes()
    for path,digest in context['input_hashes'].items():
        assert sha(Path(path)) == digest
    save(out/'manifest.json',dict(case=args.case,profile=args.profile,seed=args.seed,phase=args.phase,
         original_K=list(p.batch_count_candidates),retained_K=counts,pool_bounds=pool_bounds,
         audited_upper_bound=upper,common_lower_bound=lower,seconds=args.seconds,threads=1,
         formulation='compact',strengthening='physical_time',preprocessing=False,vehicle_symmetry=False,
         mip_start_supplied=True,historical_routes_as_start=True,route_hints_supplied=False,case_process_concurrency=2,
         gurobi_MIPFocus=3,
         stop_on_matching_valid_global_bound=True,source_hashes=source,driver_hashes=hashes,
         input_context=str(context_path),historical_information_used=True,
         budget_scope='Optimization budget only. Per-trial load, audit, build and source checks recorded separately. '
                     'Shared domain preparation and historical bound/solution generation are outside trial budget.'))
    library = read(HERE/'inputs'/args.case/'trip_library.json')
    augmentations = {}

    def fixed(child,**kwargs):
        model,ctx = build_model(child,strengthening='physical_time',preprocessing=False,vehicle_symmetry=False,**kwargs)
        ctx['pool'].LB = max(ctx['pool'].LB,pool_bounds[0])
        ctx['pool'].UB = min(ctx['pool'].UB,pool_bounds[1])
        if args.profile=='masks':
            local_library = library
            if child.operating_policy.time_tick_seconds != p.operating_policy.time_tick_seconds:
                local_library,_ = enumerate_trip_library(child)
            augmentations[len(child.operating_policy.batches)] = add_shared_route_bounds(child,model,ctx,local_library)
        model.update()
        return model,ctx

    def exclude(child):
        k = len(child.operating_policy.batches)
        return None if k in counts else dict(reason='CERTIFIED_COST_COMPETITIVE_DOMAIN',K=k,upper_bound=upper)

    build_begun = time.perf_counter()
    if len(counts)==1:
        count = counts[0]
        child = p.for_count(count)
        model,fixed_context = fixed(child,compact_e2=True)
        blocks = {count:dict(problem=child,context=fixed_context)}
        selectors = None
    else:
        model,unified = build_unified_model(p,build_fixed=fixed,compact_e2=True,count_certificate=exclude)
        blocks,selectors = unified['blocks'],unified['selectors']
    try:
        objective = model.getObjective()
        model.addConstr(objective >= lower-1e-7,name='common_proven_cost_lower_bound')
        model.addConstr(objective <= upper+1e-7,name='common_audited_cost_upper_bound')
        model.update()
        assert model.NumStart == 0
        start_evidence = apply_audited_start(model,blocks,selectors,witness)
        assert model.NumStart >= 1
        build_seconds = time.perf_counter()-build_begun
        model.Params.TimeLimit = args.seconds
        model.Params.Threads = 1
        model.Params.Seed = args.seed
        model.Params.MIPFocus = 3
        model.Params.OutputFlag = 1
        model.Params.LogToConsole = 0
        model.Params.LogFile = os.path.relpath(out/'gurobi.log',ROOT)
        incumbents,closure = [],[]
        last = -20.

        def observe(m,where):
            nonlocal last
            if where == gp.GRB.Callback.MIPSOL:
                incumbents.append(dict(seconds=m.cbGet(gp.GRB.Callback.RUNTIME),objective=m.cbGet(gp.GRB.Callback.MIPSOL_OBJ)))
            if where == gp.GRB.Callback.MIP:
                elapsed = m.cbGet(gp.GRB.Callback.RUNTIME)
                bound = finite(m.cbGet(gp.GRB.Callback.MIP_OBJBND))
                if elapsed-last >= 10:
                    save(out/'progress.json',dict(stage='optimization',case=args.case,profile=args.profile,seed=args.seed,
                         seconds=elapsed,solver_incumbent=finite(m.cbGet(gp.GRB.Callback.MIP_OBJBST)),
                         native_bound=bound,nodes=m.cbGet(gp.GRB.Callback.MIP_NODCNT)))
                    last = elapsed
                if bound is not None and abs(upper-bound)<=1e-6:
                    closure.append(dict(seconds=elapsed,valid_global_bound=bound,
                                        source='Gurobi_MIP_OBJBND_callback',audited_feasible_cost=upper))
                    m.terminate()

        print(json.dumps(dict(stage='optimization',case=args.case,profile=args.profile,seed=args.seed,
                              variables=model.NumVars,rows=model.NumConstrs+model.NumGenConstrs,
                              K=counts,N=pool_bounds,seconds=args.seconds)),flush=True)
        model.optimize(observe)
        info = read_milp_info(model,proof_scope='complete_M8_on_certified_domain_with_additive_valid_bounds',build_time=build_seconds)
        info.update(mip_start_supplied=True,historical_routes_as_start=True,MIPFocus=3,start_evidence=start_evidence)
        if model.Status in (gp.GRB.INFEASIBLE,gp.GRB.INF_OR_UNBD):
            raise RuntimeError('Audited feasible witness should extend to the strengthened model')
        result = None
        if model.SolCount:
            k = counts[0] if selectors is None else next(k for k,v in selectors.items() if v.X>.5)
            block = blocks[k]
            result = extract_solution(block['problem'],model,block['context'],dict(info))
            boundaries = [b['start'] for b in result['batches']]+[block['problem'].operating_policy.cycle_end]
            attach_calendar_decisions(p,result,k,boundaries)
            result['audit'] = audit_solution(p,result)
            accept_milp_audit(result,model.ObjVal)
            assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
            info.update(objective=result['info']['objective'],mip_gap=result['info']['mip_gap'])
            save(out/'native_result.json',result)
        best = result if result is not None and info['objective']<=upper else witness
        final_audit = audit_solution(p,best)
        assert final_audit['valid'] and final_audit['periodic_replay']['valid']
        best_upper = final_audit['objective']
        global_lower = lower
        if info['best_bound'] is not None:
            global_lower = max(global_lower,min(upper,info['best_bound']))
        if closure:
            global_lower = max(global_lower,min(upper,closure[0]['valid_global_bound']))
        assert global_lower<=best_upper+1e-6
        closed = abs(best_upper-global_lower)<=1e-6
        assert source_hashes()==source
        assert all(sha(Path(path))==digest for path,digest in hashes.items())
        assert all(sha(Path(path))==digest for path,digest in context['input_hashes'].items())
        save(out/'best_audited_result.json',best)
        info.update(incumbents=incumbents,certificate_callback=closure,augmentations=augmentations)
        save(out/'native_summary.json',info)
        summary = dict(case=args.case,profile=args.profile,phase=args.phase,seed=args.seed,native_status=info['status'],
            global_optimality_certified=closed,proof_seconds=closure[0]['seconds'] if closure else (info['mip_runtime'] if closed else None),
            audited_upper_bound=best_upper,global_lower_bound=global_lower,gap_over_upper=max(0.,best_upper-global_lower)/abs(best_upper),
            mip_runtime=info['mip_runtime'],mip_work=info['mip_work'],nodes=info['node_count'],build_seconds=build_seconds,
            total_wall_seconds=time.perf_counter()-begun,variables=info['variables'],constraints=info['constraints'],
            first_solver_incumbent_seconds=incumbents[0]['seconds'] if incumbents else None,
            retained_K=counts,retained_N=pool_bounds,original_K=list(p.batch_count_candidates),
            historical_U=upper,common_L=lower,input_and_sources_unchanged=True,business_audit_valid=True,two_cycle_replay_valid=True,
            termination_reason='AUDITED_UPPER_EQUALS_VALID_GLOBAL_LOWER' if closure else info['status'])
        save(out/'summary.json',summary)
        save(out/'progress.json',dict(stage='complete',**summary))
        print(json.dumps(summary),flush=True)
    finally:
        model.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case',required=True,choices=['S03','S08','S05'])
    parser.add_argument('--profile',default='warm_focus',choices=['warm_focus'])
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--seconds',type=float,default=180)
    parser.add_argument('--phase',choices=['pilot','holdout'],default='pilot')
    args = parser.parse_args()
    try:
        run(args)
    except Exception as error:
        out = HERE/'runs'/args.phase/args.case/args.profile/f's{args.seed}'
        if out.is_dir():
            save(out/'error.json',dict(error=str(error),traceback=traceback.format_exc()))
        raise


if __name__=='__main__':
    main()
