"""Matched candidate trial using only the old frozen U/L and input evidence."""
import argparse
import time
import traceback

from common import HERE,ROOT,OLD,load,module,read,save,sha
from models.common.cycle.audit import audit_solution
from experiments.runs.probe_model8_physical_bounds import source_hashes


def run(case,profile,seed,seconds,phase):
    begun=time.perf_counter()
    out=HERE/'runs'/phase/case/profile/f's{seed}'
    out.mkdir(parents=True,exist_ok=False)
    try:
        p,witness,context,library,phase_module=load(case)
        folder=OLD/'inputs'/case
        algorithm_path=(OLD/'decomposition_compact.py' if profile=='baseline' else
                        HERE/'decomposition_sync.py' if profile=='sync' else HERE/'decomposition_core.py')
        engine=module(algorithm_path,'engine_'+profile)
        files=[HERE/'run_trial.py',HERE/'common.py',algorithm_path,OLD/'route_bounds.py',folder/'context.json',
            folder/'trip_library.json',folder/'reduction_certificate.json',
            ROOT/'experiments/runs/research_model8_shared_relaxation.py',
            ROOT/'output/experiments/m8_s04_reduction_validation_20261026_v1/phase_bound.py']
        if profile!='baseline':
            files.append(HERE/'master_sync.py')
        if profile in ('template','core'):
            files.append(HERE/'follower_templates.py')
        for k in context['reduced_K']:
            files.extend([folder/f'filtered_library_K{k}.json',folder/f'screening_K{k}.json'])
        hashes={str(path):sha(path) for path in files}
        sources=source_hashes()
        save(out/'manifest.json',dict(case=case,profile=profile,phase=phase,seed=seed,seconds=seconds,threads=1,
            original_K=list(p.batch_count_candidates),retained_K=context['reduced_K'],retained_N=context['reduced_N'],
            initial_U=context['audited_upper_bound'],common_L=context['common_lower_bound'],
            input_context=str(folder/'context.json'),driver_hashes=hashes,source_hashes=sources,
            historical_information_used=True,newly_proven_optimum_used_as_lower_bound=False,
            budget_scope='Entire algorithm construction, refinement and optimization loop; shared prior preparation excluded.'))
        last=-30.
        def observe(event):
            nonlocal last
            save(out/'progress.json',dict(stage='optimization',case=case,profile=profile,seed=seed,**event))
            with (out/'trace.jsonl').open('a',encoding='utf-8') as stream:
                import json
                stream.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n')
            if event['elapsed']-last>=30:
                print(f"{case} {profile} seed={seed} seconds={event['elapsed']:.1f} L={event['global_lower_bound']:.9f}",flush=True)
                last=event['elapsed']
        kwargs=dict(on_event=observe)
        if profile in ('template','core'):
            kwargs['generalize_conflicts']=profile=='core'
        result,info=engine.solve(p,witness,context['reduced_K'],context['reduced_N'][0],
            context['common_lower_bound'],library,phase_module,seconds,seed,**kwargs)
        audit=audit_solution(p,result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert source_hashes()==sources
        assert all(sha(path)==digest for path,digest in hashes.items())
        assert all(sha(path)==digest for path,digest in context['input_hashes'].items())
        save(out/'best_audited_result.json',result)
        save(out/'algorithm_summary.json',info)
        row=dict(case=case,profile=profile,phase=phase,seed=seed,seconds_budget=seconds,
            algorithm_status=info['status'],global_optimality_certified=info['global_optimality_certified'],
            proof_seconds=info['elapsed_seconds'] if info['global_optimality_certified'] else None,
            algorithm_seconds=info['elapsed_seconds'],total_wall_seconds=time.perf_counter()-begun,
            audited_upper_bound=info['audited_upper_bound'],global_lower_bound=info['global_lower_bound'],gap_over_upper=info['gap_over_upper'],
            patterns=info['patterns'],iterations=info['iterations'],cuts=info['cuts'],
            master_seconds=info['master_seconds'],follower_seconds=info['follower_seconds'],mip_work=info['mip_work'],
            original_K=list(p.batch_count_candidates),historical_U=context['audited_upper_bound'],common_L=context['common_lower_bound'],
            source_and_inputs_unchanged=True,business_audit_valid=True,two_cycle_replay_valid=True,
            native_status='MULTIPLE_NATIVE_MASTER_AND_FOLLOWER_SOLVES')
        for key in ('template_builds','template_build_seconds','iis_seconds','verification_seconds','generalized_cuts','sync_bounds'):
            if key in info:
                row[key]=info[key]
        save(out/'summary.json',row)
        save(out/'progress.json',dict(stage='complete',**row))
        print(row,flush=True)
    except Exception as error:
        save(out/'error.json',dict(error=str(error),traceback=traceback.format_exc()))
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--case',choices=['S03','S08','S05'],required=True)
    parser.add_argument('--profile',choices=['baseline','sync','template','core'],required=True)
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--seconds',type=float,default=180)
    parser.add_argument('--phase',default='pilot')
    args=parser.parse_args()
    run(args.case,args.profile,args.seed,args.seconds,args.phase)
