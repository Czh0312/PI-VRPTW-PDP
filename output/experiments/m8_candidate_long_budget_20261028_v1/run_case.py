"""One-hour follow-up of the previously frozen M8 compact decomposition.

The algorithm is imported without modification. Runs start from the same old
audited witness and valid L; they do not resume or mix prior 300-second cuts.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import traceback
import zipfile

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
PREVIOUS=ROOT/'output/experiments/m8_stability_20261028_v1'
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(PREVIOUS))

from decomposition_compact import solve
from experiments.runs.probe_model8_physical_bounds import source_hashes
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from tools.configuration.loader import load_routing_case


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path,value):
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temp.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def memory_mb():
    try:
        import os
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss/(1024*1024)
    except Exception:
        return None


def run(case,seed,seconds):
    begun=time.perf_counter()
    out=HERE/case
    out.mkdir(parents=True,exist_ok=False)
    try:
        context_path=PREVIOUS/'inputs'/case/'context.json'
        context=read(context_path)
        p=load_instance(*load_routing_case(Path(context['case_path']),use_pool_config=False))
        witness=read(Path(context['witness_path']))
        audit=audit_solution(p,witness)
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert abs(audit['objective']-context['audited_upper_bound'])<=1e-6
        assert context['reduced_N'][0]==context['reduced_N'][1]
        for k in context['reduced_K']:
            policy=p.for_count(k).operating_policy
            assert policy.time_tick_seconds==p.operating_policy.time_tick_seconds
            assert policy.time_tick_subdivision==p.operating_policy.time_tick_subdivision
        plan=read(PREVIOUS/'holdout_plan_A.json')
        assert sha(PREVIOUS/'decomposition_compact.py')==plan['source_hashes']['decomposition_compact.py']
        folder=context_path.parent
        original=read(folder/'trip_library.json')
        allowed=set().union(*[{row['mask'] for row in read(folder/f'filtered_library_K{k}.json')}
                              for k in context['reduced_K']])
        library=[row for row in original if row['mask'] in allowed]
        phase_path=ROOT/'output/experiments/m8_s04_reduction_validation_20261026_v1/phase_bound.py'
        phase=module(phase_path,'candidate_long_phase')
        short_path=PREVIOUS/'runs/holdout'/case/'compact_decomposition'/f's{seed}'/'summary.json'
        files=[Path(__file__),PREVIOUS/'decomposition_compact.py',PREVIOUS/'route_bounds.py',
               PREVIOUS/'holdout_plan_A.json',PREVIOUS/'validation.json',context_path,
               folder/'trip_library.json',folder/'reduction_certificate.json',phase_path,short_path]
        for k in context['reduced_K']:
            files.extend([folder/f'screening_K{k}.json',folder/f'filtered_library_K{k}.json'])
        hashes={str(path):sha(path) for path in files}
        sources=source_hashes()
        assert all(sha(Path(path))==digest for path,digest in context['input_hashes'].items())
        save(out/'manifest.json',dict(case=case,seed=seed,seconds=seconds,threads=1,max_concurrent_cases=2,
            algorithm='Unmodified frozen compact_decomposition',
            algorithm_sha256=sha(PREVIOUS/'decomposition_compact.py'),
            original_K=list(p.batch_count_candidates),retained_K=context['reduced_K'],retained_N=context['reduced_N'],
            audited_upper_bound=audit['objective'],common_lower_bound=context['common_lower_bound'],
            input_context=str(context_path),historical_information_used=True,mip_start_supplied=False,
            driver_hashes=hashes,source_hashes=sources,short_comparison_summary=str(short_path),
            seed_selection='Earlier 300-second seed with smallest final gap for this case; targeted optimality follow-up, '
                           'not a new independent stability test.',
            restart='New run from original frozen U/L, without replaying the previous short-run cuts.',
            budget_scope='3600-second full master/follower construction and optimization loop; '
                         'historical bound generation and shared input preparation excluded.'))
        save(out/'initial_audited_result.json',witness)
        # Snapshot source and input proof bytes, never licence files.
        snapshot=dict(hashes)
        snapshot.update({str((ROOT/path).resolve()):digest for path,digest in sources.items()})
        snapshot.update(context['input_hashes'])
        with zipfile.ZipFile(out/'frozen_source_inputs.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
            for path,digest in sorted(snapshot.items()):
                target=Path(path)
                assert sha(target)==digest
                archive.write(target,str(target.relative_to(ROOT)))
        last_print=-60.
        last_bound=context['common_lower_bound']

        def on_event(event):
            nonlocal last_print,last_bound
            progress=dict(stage='optimization',case=case,seed=seed,**event,rss_mb=memory_mb())
            save(out/'progress.json',progress)
            with (out/'trace.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(progress,ensure_ascii=False,allow_nan=False)+'\n')
            if event['elapsed']-last_print>=45 or event['global_lower_bound']-last_bound>=.02:
                print(json.dumps({key:progress.get(key) for key in ['case','seed','elapsed','iteration',
                    'global_lower_bound','audited_upper_bound','native_status','rss_mb']}),flush=True)
                last_print=event['elapsed']
                last_bound=event['global_lower_bound']

        print(json.dumps(dict(stage='started',case=case,seed=seed,seconds=seconds,
            initial_U=audit['objective'],initial_L=context['common_lower_bound'])),flush=True)
        result,info=solve(p,witness,context['reduced_K'],context['reduced_N'][0],context['common_lower_bound'],
                          library,phase,seconds,seed,on_event)
        final_audit=audit_solution(p,result)
        assert final_audit['valid'] and final_audit['periodic_replay']['valid']
        assert source_hashes()==sources
        assert all(sha(Path(path))==digest for path,digest in hashes.items())
        assert all(sha(Path(path))==digest for path,digest in context['input_hashes'].items())
        save(out/'best_audited_result.json',result)
        save(out/'decomposition_summary.json',info)
        short=read(short_path)
        summary=dict(case=case,seed=seed,seconds_budget=seconds,
            algorithm_status=info['status'],native_status='MULTIPLE_NATIVE_MASTER_AND_FOLLOWER_SOLVES',
            global_optimality_certified=info['global_optimality_certified'],
            proof_seconds=info['elapsed_seconds'] if info['global_optimality_certified'] else None,
            audited_upper_bound=info['audited_upper_bound'],global_lower_bound=info['global_lower_bound'],
            gap_over_upper=info['gap_over_upper'],algorithm_seconds=info['elapsed_seconds'],
            total_wall_seconds=time.perf_counter()-begun,mip_work=info['mip_work'],
            master_seconds=info['master_seconds'],follower_seconds=info['follower_seconds'],
            patterns=info['patterns'],iterations=info['iterations'],cuts=info['cuts'],
            original_K=list(p.batch_count_candidates),retained_K=context['reduced_K'],retained_N=context['reduced_N'],
            business_audit_valid=True,two_cycle_replay_valid=True,input_and_sources_unchanged=True,
            short_gap_over_upper=short['gap_over_upper'],short_global_lower_bound=short['global_lower_bound'],
            historical_U=context['audited_upper_bound'],common_L=context['common_lower_bound'])
        save(out/'summary.json',summary)
        save(out/'progress.json',dict(stage='complete',**summary))
        print(json.dumps(summary),flush=True)
    except Exception as error:
        save(out/'error.json',dict(error=str(error),traceback=traceback.format_exc(),
                                 elapsed=time.perf_counter()-begun,rss_mb=memory_mb()))
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--case',choices=['S03','S08'],required=True)
    parser.add_argument('--seed',type=int,required=True)
    parser.add_argument('--seconds',type=float,default=3600)
    args=parser.parse_args()
    run(args.case,args.seed,args.seconds)
