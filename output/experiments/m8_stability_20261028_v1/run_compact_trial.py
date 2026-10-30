"""Certified pattern-decomposition stability trial on the frozen inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0,str(ROOT))
from prepare import read,save,module
from decomposition_compact import solve
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from tools.configuration.loader import load_routing_case
from experiments.runs.probe_model8_physical_bounds import source_hashes


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    begun = time.perf_counter()
    out = HERE/'runs'/args.phase/args.case/'compact_decomposition'/f's{args.seed}'
    out.mkdir(parents=True,exist_ok=False)
    folder = HERE/'inputs'/args.case
    context = read(folder/'context.json')
    p = load_instance(*load_routing_case(Path(context['case_path']),use_pool_config=False))
    witness = read(Path(context['witness_path']))
    assert context['reduced_N'][0]==context['reduced_N'][1]
    original_library = read(folder/'trip_library.json')
    allowed = {k:{row['mask'] for row in read(folder/f'filtered_library_K{k}.json')} for k in context['reduced_K']}
    union = set().union(*allowed.values())
    library = [row for row in original_library if row['mask'] in union]
    phase_path = ROOT/'output/experiments/m8_s04_reduction_validation_20261026_v1/phase_bound.py'
    phase = module(phase_path,'decomposition_phase_bound')
    files = [Path(__file__),HERE/'decomposition_compact.py',HERE/'route_bounds.py',folder/'context.json',
             folder/'trip_library.json',phase_path]
    for k in context['reduced_K']:
        files.extend([folder/f'screening_K{k}.json',folder/f'filtered_library_K{k}.json'])
    hashes = {str(path):sha(path) for path in files}
    sources = source_hashes()
    save(out/'manifest.json',dict(case=args.case,profile='compact_decomposition',phase=args.phase,seed=args.seed,
        original_K=list(p.batch_count_candidates),retained_K=context['reduced_K'],retained_N=context['reduced_N'],
        audited_upper_bound=context['audited_upper_bound'],common_lower_bound=context['common_lower_bound'],
        seconds=args.seconds,threads=1,case_process_concurrency=2,mip_start_supplied=False,
        historical_information_used=True,driver_hashes=hashes,source_hashes=sources,input_context=str(folder/'context.json'),
        original_columns=len(original_library),union_retained_columns=len(library),
        budget_scope='Entire master/follower construction and optimization loop is inside seconds; '
                    'shared preparation, input load, source hashes and final audit recorded separately.'))

    def on_event(event):
        save(out/'progress.json',dict(stage='optimization',case=args.case,profile='compact_decomposition',seed=args.seed,**event))
        with (out/'trace.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n')

    result,info = solve(p,witness,context['reduced_K'],context['reduced_N'][0],context['common_lower_bound'],
                       library,phase,args.seconds,args.seed,on_event)
    audit = audit_solution(p,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert source_hashes()==sources
    assert all(sha(Path(path))==digest for path,digest in hashes.items())
    assert all(sha(Path(path))==digest for path,digest in context['input_hashes'].items())
    save(out/'best_audited_result.json',result)
    save(out/'decomposition_summary.json',info)
    summary = dict(case=args.case,profile='compact_decomposition',phase=args.phase,seed=args.seed,
        native_status='MULTIPLE_NATIVE_MASTER_AND_FOLLOWER_SOLVES',algorithm_status=info['status'],
        global_optimality_certified=info['global_optimality_certified'],
        proof_seconds=info['elapsed_seconds'] if info['global_optimality_certified'] else None,
        audited_upper_bound=info['audited_upper_bound'],global_lower_bound=info['global_lower_bound'],
        gap_over_upper=info['gap_over_upper'],mip_runtime=info['master_seconds']+info['follower_seconds'],
        mip_work=info['mip_work'],build_seconds=None,total_wall_seconds=time.perf_counter()-begun,
        patterns=info['patterns'],iterations=info['iterations'],cuts=info['cuts'],
        first_solver_incumbent_seconds=None,original_K=list(p.batch_count_candidates),
        historical_U=context['audited_upper_bound'],common_L=context['common_lower_bound'],
        input_and_sources_unchanged=True,business_audit_valid=True,two_cycle_replay_valid=True)
    save(out/'summary.json',summary)
    save(out/'progress.json',dict(stage='complete',**summary))
    print(json.dumps(summary),flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case',required=True,choices=['S03','S08','S05'])
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--seconds',type=float,default=180)
    parser.add_argument('--phase',choices=['pilot','holdout'],default='pilot')
    args = parser.parse_args()
    try:
        run(args)
    except Exception as error:
        out = HERE/'runs'/args.phase/args.case/'compact_decomposition'/f's{args.seed}'
        if out.is_dir():
            save(out/'error.json',dict(error=str(error),traceback=traceback.format_exc()))
        raise


if __name__=='__main__':
    main()
