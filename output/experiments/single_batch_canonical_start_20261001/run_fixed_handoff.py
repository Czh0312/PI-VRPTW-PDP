"""Same route identities and certificate; only delivery-start timing changes.

Supplementary full-MILP-only comparison. Cached CG/pool construction is excluded
from 30 seconds; normalization, complete model construction and solve share it.
Every job uses final_long's immutable runtime and independently audited inputs.
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
import time

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
FROZEN=OUT/'stable_long'
SNAPSHOT=FROZEN/'source_snapshot'
sys.path.insert(0,str(SNAPSHOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def worker(job_path):
    from models.routing.solvers.exact.solver import solve_exact
    from models.routing.solvers.exact.single_batch_schedule_start import canonical_delivery_start
    job=json.loads(Path(job_path).read_text())
    config,fleet=load_routing_case(job['case_path'])
    p=load_instance(config,fleet)
    initial=json.loads(Path(job['start_path']).read_text())
    cert=json.loads(Path(job['certificate_path']).read_text())
    assert sha(Path(job['start_path']))==job['start_sha256']
    assert sha(Path(job['certificate_path']))==job['certificate_sha256']
    assert cert['problem_sha256']==problem_fingerprint(p)
    assert audit_solution(p,initial)['valid']
    begin=time.perf_counter()
    normalization=None
    warm=initial
    if job['canonical']:
        transformed,normalization=canonical_delivery_start(p,initial,deadline=begin+job['budget'])
        if transformed is not None:
            assert normalization['vehicle_labels_changed']==0 and normalization['trip_slots_changed']==0
            original_identity={t['trip_id']:(t['vehicle_id'],t.get('trip_index')) for layer in ('e1','e2') for t in initial[layer+'_trips']}
            changed_identity={t['trip_id']:(t['vehicle_id'],t.get('trip_index')) for layer in ('e1','e2') for t in transformed[layer+'_trips']}
            assert original_identity==changed_identity
            warm=transformed
    result=solve_exact(p,time_limit=max(.001,job['budget']-(time.perf_counter()-begin)),
        seed=job['seed'],initial_result=warm,_single_batch_route_certificate=cert,verbose=True)
    wall=time.perf_counter()-begin
    audited=audit_solution(p,result)
    assert audited['valid'] and audited['periodic_replay']['valid']
    assert audited['objective']<=audit_solution(p,initial)['objective']+1e-6
    result['audit']=audited
    result['info']['route_bound_certificate']=cert
    write(Path(job['result_path']),result)
    info=result['info']
    # Scalar route certificate stays a valid lower bound independent of starts.
    bound=max(cert['bound'],info.get('best_bound') or 0)
    assert bound<=audited['objective']+1e-5
    write(Path(job['row_path']),dict(method='canonical' if job['canonical'] else 'raw',
        seed=job['seed'],budget=job['budget'],budget_stage='supplementary full MILP including normalization and construction; cached root/pool excluded',
        objective=audited['objective'],bound=bound,gap=(audited['objective']-bound)/max(1e-9,abs(audited['objective'])),
        runtime=wall,valid=True,replay=True,proven_optimal=info['proven_optimal'],
        proven_infeasible=info['proven_infeasible'],start_supplied=info.get('mip_start_supplied'),
        nodes=info.get('mip_node_count'),normalization=normalization,
        start_sha256=job['start_sha256'],certificate_sha256=job['certificate_sha256']))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker')
    args=parser.parse_args()
    if args.worker:
        return worker(args.worker)
    out=OUT/'fixed_handoff'
    assert not out.exists(),'Refuse overwrite or retry'
    out.mkdir()
    case=next(FROZEN.glob('*/case.json'))
    manifest=json.loads((FROZEN/'manifest.json').read_text())
    for path,expected in manifest['source_sha256'].items():
        assert sha(SNAPSHOT/path)==expected
    previous=ROOT/'output/experiments/single_batch_compact_pool_20261001'
    common=[]
    for seed in (42,43):
        initial_path=previous/f'fixed_pool/compact_{seed}/result.json'
        certificate_source=previous/f'aggregate_long/zhou_Set2_100-5-1b_subset30_M2/columns_compact_{seed}/result.json'
        start=out/f'start_{seed}.json'
        certificate=out/f'certificate_{seed}.json'
        write(start,json.loads(initial_path.read_text()))
        write(certificate,json.loads(certificate_source.read_text())['info']['route_bound_certificate'])
        common.append(dict(seed=seed,case_path=str(case),budget=30,
            start_path=str(start),start_sha256=sha(start),certificate_path=str(certificate),certificate_sha256=sha(certificate),
            original_start=str(initial_path),original_certificate=str(certificate_source)))
    write(out/'manifest.json',dict(source_snapshot=str(SNAPSHOT),source_sha256=manifest['source_sha256'],
        input_sha256=manifest['input_sha256'],jobs=common,script_sha256=sha(Path(__file__)),
        execution='sequential isolated subprocesses; same routes, audited start and certificate per seed; no retries',
        scope='supplementary full-MILP-only 30 seconds; excludes cached root/pool generation'))
    rows=[]
    for item in common:
        seed=item['seed']
        for canonical in ([False,True] if seed==42 else [True,False]):
            run=out/f'{"canonical" if canonical else "raw"}_{seed}'
            run.mkdir()
            job=dict(**item,canonical=canonical,result_path=str(run/'result.json'),row_path=str(run/'row.json'))
            path=run/'job.json'
            write(path,job)
            proc=subprocess.run([sys.executable,str(Path(__file__)),'--worker',str(path)],
                cwd=SNAPSHOT,capture_output=True,text=True,timeout=item['budget']+30)
            (run/'solver.log').write_text(proc.stdout+'\n'+proc.stderr)
            if proc.returncode:
                write(out/'failure.json',dict(job=job,returncode=proc.returncode,stderr=proc.stderr))
                raise RuntimeError('Fixed handoff failed; artifacts retained; no retry')
            row=json.loads((run/'row.json').read_text())
            rows.append(row)
            write(out/'comparison.json',rows)
            print(row['method'],seed,row['objective'],row['bound'],row['gap'],flush=True)


if __name__=='__main__':
    main()
