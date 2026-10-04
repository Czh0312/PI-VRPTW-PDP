"""Fresh fixed-dual complete pricing comparisons, all setup inside total5s."""
from pathlib import Path
from datetime import datetime,timezone
import argparse
import os
import shutil
import subprocess
import sys
import time
from common import OUT,NIGHT,ROOT,read,write,sha,external,gate,load_case,frozen_cases


def worker(path):
    job=read(path);begun=time.perf_counter();deadline=begun+job['total_budget']
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
    from models.routing.solvers.exact.single_batch_pickup_bound import pickup_contexts,project_original_schedule
    from balance import transformed_prices,price_all
    folder=Path(job['case_path']).parent;p=load_case(folder);start=read(job['start_path'])
    a=audit_solution(p,start);assert a['valid'] and a['periodic_replay']['valid']
    _,contexts,check=pickup_contexts(p);assert check['passed']
    cols=project_original_schedule(p,start,contexts);assert sum(len(c['tasks']) for _,c in cols)==40
    history=read(job['fixed_history_path']);counts=job['maximum_trips']
    assert problem_fingerprint(p)==job['problem_sha256'] and sha(job['start_path'])==job['start_sha256']
    result=price_all(p,contexts,history,counts,balanced=job['balanced'],seed=job['seed'],deadline=deadline,
                     call_cap=job['call_cap'],verbose=True)
    elapsed=time.perf_counter()-begun
    assert result['e2_bound']<=a['distances']['e2']+1e-6 and not result['old_rho_reused']
    result.update(problem_sha256=problem_fingerprint(p),maximum_trips=counts,
                  original_schedule_audited=True,periodic_replay=True,full_tasks=40,
                  runtime=elapsed,original_e2_distance=a['distances']['e2'],original_upper=a['objective'],
                  proof_scope='B1M4_fixed_dual_complete_mixed_task_pricing_only',proven_integer_optimal=False)
    assert not Path(job['result_path']).exists();write(job['result_path'],result)
    row={k:result[k] for k in ['e2_bound','corrected_e2_bound','raw_corrected','explicit_dual_objective','margin',
         'pricing_complete','runtime','build_runtime','pricing_runtime','pricing_lower_bounds','old_rho_reused']}
    row.update(case=job['case'],historical_budget=job['historical_budget'],seed=job['seed'],balanced=job['balanced'],
               total_budget=job['total_budget'],status='COMPLETE' if result['pricing_complete'] else 'PRICING_LIMIT',
               problem_sha256=job['problem_sha256'],start_sha256=job['start_sha256'],
               fixed_history_sha256=sha(job['fixed_history_path']),proven_integer_optimal=False,
               pricing_work=sum(x['work'] for x in result['pricing'].values()),
               source_snapshot_sha256=job['source_snapshot_sha256'])
    write(job['row_path'],row)


def prepare():
    from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
    assert read(OUT/'validation.json')['original_large40task_projections']==2
    target=OUT/'prices5';target.mkdir();jobs=[]
    for batch,case,folder in frozen_cases():
        p=load_case(folder)
        for seed in (42,43):
            for budget in ((15,30) if seed==42 else (30,15)):
                old=folder/f'baseline_{budget}_{seed}'/'result.json';data=read(old)
                history=[h for h in data['history'] if h['phase']==2][-1]
                assert data['problem_sha256']==problem_fingerprint(p)
                fixed=target/case/f'state_{budget}_{seed}';fixed.mkdir(parents=True)
                write(fixed/'history.json',history)
                write(fixed/'origin.json',dict(path=str(old),sha256=sha(old),rule='deterministic LAST phase2',
                    selected_iteration=history['iteration'],all_prior_iterations_retained=True))
                for balanced in ((False,True) if seed==42 else (True,False)):
                    run=fixed/('balanced' if balanced else 'original');run.mkdir()
                    job=dict(case=case,historical_budget=budget,seed=seed,balanced=balanced,total_budget=5,
                        call_cap=1.0,case_path=str(folder/'case_balance_rebased.json'),start_path=str(folder/'start.json'),
                        start_sha256=sha(folder/'start.json'),fixed_history_path=str(fixed/'history.json'),
                        maximum_trips=data['maximum_trips'],problem_sha256=problem_fingerprint(p),
                        source_snapshot_sha256=sha(OUT/'prepared_manifest.json'),
                        result_path=str(run/'result.json'),row_path=str(run/'row.json'))
                    write(run/'job.json',job);jobs.append(str(run/'job.json'))
    assert len(jobs)==16
    for name in ('balance.py','common.py','validate.py','compare.py'):
        shutil.copy2(OUT/name,target/name)
    write(target/'jobs.json',jobs)
    write(target/'manifest.json',dict(jobs=16,seeds=[42,43],total_budget=5,call_cap=1.0,threads=1,
        fixed_state_rule='LAST phase2 of each8 baseline frozen histories',
        driver_hashes={n:sha(OUT/n) for n in ('balance.py','common.py','validate.py','compare.py')},
        prepared_manifest_sha256=sha(OUT/'prepared_manifest.json'),validation_sha256=sha(OUT/'validation.json'),
        plan_sha256=sha(OUT/'plan.json'),preparation_not_price_timing=True,
        runtime_budget='Input loading/original audit/context/projection/price transform/all MIP construction/new pricing charged to5 seconds; imports before worker entry excluded and total process wall recorded',
        limitation='Fixed-dual mechanism test; no full root/integer speed proof or default adoption'))
    return target


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--worker');args=parser.parse_args()
    if args.worker:return worker(Path(args.worker))
    target=OUT/'prices5'
    if not target.exists():target=prepare()
    manifest=read(target/'manifest.json')
    for n,h in manifest['driver_hashes'].items():assert sha(OUT/n)==h
    rows=read(target/'comparison.json') if (target/'comparison.json').exists() else []
    for index,path in enumerate(read(target/'jobs.json'),1):
        path=Path(path);job=read(path)
        if Path(job['row_path']).exists():
            continue
        assert not path.with_name('failed.json').exists(),'Failed row retained; do not rerun'
        before=gate(f'fixedprice job{index}')
        p=read(NIGHT/'checkpoint.json');p.update(status='M4_PRICE_BALANCE_FIXED16_RUNNING',
            active_process=dict(kind='fixed_dual_complete_pricing',conductor_pid=os.getpid(),index=index,total=16,job=job),
            balance_waiting_pid=None,balance_waiting_stage=None,updated_utc=datetime.now(timezone.utc).isoformat())
        write(NIGHT/'checkpoint.json',p);started=time.perf_counter()
        try:
            run=subprocess.run([sys.executable,str(OUT/'compare.py'),'--worker',str(path)],
                cwd=OUT,capture_output=True,text=True,timeout=35)
            path.with_name('solver.log').write_text(run.stdout+'\n'+run.stderr)
            if run.returncode:
                write(path.with_name('failed.json'),dict(returncode=run.returncode,job=job,failed=True))
                raise RuntimeError('Failed pricing job retained; no retries')
        except subprocess.TimeoutExpired as error:
            path.with_name('solver.log').write_bytes((error.stdout or b'')+(error.stderr or b''))
            write(path.with_name('failed.json'),dict(error='worker timeout',job=job));raise
        write(path.with_name('external_load.json'),dict(before=before,after=external(),worker_wall=time.perf_counter()-started))
        row=read(job['row_path']);rows.append(row);write(target/'comparison.json',rows)
        print(index,'/16',job['case'],job['historical_budget'],job['seed'],job['balanced'],
              'rho',row['pricing_lower_bounds'],'corrected',row['corrected_e2_bound'],flush=True)
    assert len(rows)==16
    p=read(NIGHT/'checkpoint.json');p.update(status='M4_PRICE_BALANCE_FIXED16_COMPLETE',active_process=None,
        next_action='Independently verify fresh prices/certificates, all16 originals and matched pairs before any mechanism/adoption claim')
    write(NIGHT/'checkpoint.json',p)


if __name__=='__main__':main()
