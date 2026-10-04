"""Five-site complete E1 enumeration: frozen joint UB + global CG bound."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD = ROOT/'output/experiments/model5_gap_target_20261001'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def worker(job):
    sys.path.insert(0, str(HERE/'source_snapshot'))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.variants.model_5.exact import reoptimize_route_pool, route_cover_certificate
    p = load_instance(*load_routing_case(job['case']))
    initial = json.loads(Path(job['initial_record']).read_text())['result']
    before = audit_solution(p, initial)
    assert before['valid'] and before['periodic_replay']['valid']
    started = time.perf_counter()
    result = reoptimize_route_pool(p, initial, time_limit=job['pool_time'],seed=42,
        verbose=True, diagnostics_dir=HERE/job['id']/'pool')
    result = json.loads(json.dumps(result))
    audited = audit_solution(p, result)
    assert audited['valid'] and audited['periodic_replay']['valid']
    assert audited['objective'] <= before['objective']+1e-6
    assert result['info']['best_bound'] is None and result['info']['mip_gap'] is None
    assert not result['info']['proven_optimal']
    checkpoint = dict(job=job, result=result, independent_audit=audited,
        initial_audit=before, lower=job['previous_lower'],
        gap_percent=100*(audited['objective']-job['previous_lower'])/abs(audited['objective']))
    write(HERE/(job['id']+'_upper.json'),checkpoint)
    print('AUDITED_UPPER',audited['objective'],'GAP',checkpoint['gap_percent'],flush=True)
    cert = route_cover_certificate(p, initial_result=result, time_limit=job['certificate_time'],
        e1_time_limit=job['e1_certificate_time'], seed=42, verbose=True,
        diagnostics_dir=HERE/job['id']/'pricing')
    assert not cert['proven_optimal'] and not cert['proven_infeasible']
    lower = max(job['previous_lower'],cert['bound'])
    assert lower <= audited['objective']+1e-6
    write(HERE/(job['id']+'.json'),dict(**checkpoint, certificate=cert,
        best_global_lower=lower,
        final_gap_percent=100*(audited['objective']-lower)/abs(audited['objective']),
        wall_seconds=time.perf_counter()-started))


if len(sys.argv)>1:
    worker(json.loads(sys.argv[1]))
else:
    source = HERE/'source_snapshot'
    source.mkdir(exist_ok=False)
    for f in [*ROOT.glob('models/**/*.py'), *ROOT.glob('configuration/*.py')]:
        target=source/f.relative_to(ROOT)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(f,target)
    modifications={}
    # Only the performance gate is extended; complete enumeration and all
    # synchronization constraints remain byte-identical to the existing model.
    for name in ('multibatch_e1_inventory.py','multibatch_route_pool.py'):
        file=source/'models/routing/solvers/exact'/name
        text=file.read_text()
        if name=='multibatch_e1_inventory.py':
            updated=text.replace('active <= 4','active <= 5').replace('active > 4','active > 5')
            updated=updated.replace('limited to four active satellites','limited to five active satellites')
        else:
            updated=text.replace('<= 4 and problem.operating_policy.e1_turnaround_time','<= 5 and problem.operating_policy.e1_turnaround_time')
        assert updated!=text
        file.write_text(updated)
        modifications[str(file.relative_to(source))]=dict(before_sha256=hashlib.sha256(text.encode()).hexdigest(),
            after_sha256=hashlib.sha256(updated.encode()).hexdigest())
    old=json.loads((OLD/'joint_pool_validation.json').read_text())
    jobs=[]
    for c in old['cases']:
        if c['customers']==100:
            payload=json.loads(Path(c['result_path']).read_text())
            jobs.append(dict(id='100_'+c['pool'],case=payload['job']['case'],
                initial_record=c['result_path'],previous_upper=c['upper'],previous_lower=c['lower'],
                previous_certificate=c['certificate_path'],pool_time=120,certificate_time=120,
                e1_certificate_time=20,seed=42))
    write(HERE/'plan.json',dict(target_gap_percent=5,jobs=jobs,
        scope='Two observed 100-customer B3 development cases. Sequential extra-budget hybrid diagnostics, not an equal-budget ALNS comparison.',
        mode='continuous',default_alns_change=False,
        bound_rule='Use only complete-domain E1 relaxation and certified complete-category E2 pricing. Restricted pool bounds never count globally.',
        modifications=modifications))
    write(HERE/'manifest.json',dict(jobs=jobs,
        source_sha256={str(f.relative_to(source)):hashlib.sha256(f.read_bytes()).hexdigest() for f in source.rglob('*.py')},
        input_sha256={str(Path(j[k])):hashlib.sha256(Path(j[k]).read_bytes()).hexdigest()
            for j in jobs for k in ('case','initial_record','previous_certificate')},
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    rows=[]
    for job in jobs:
        with (HERE/(job['id']+'.log')).open('w') as log:
            completed=subprocess.run([sys.executable,str(Path(__file__)),json.dumps(job)],
                cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=job['pool_time']+job['certificate_time']+30,
                env={**os.environ,'PYTHONHASHSEED':'0'})
        row=dict(id=job['id'],exit=completed.returncode)
        final=HERE/(job['id']+'.json')
        if completed.returncode==0 and final.exists():
            d=json.loads(final.read_text())
            row.update(upper=d['independent_audit']['objective'],lower=d['best_global_lower'],
                gap_percent=d['final_gap_percent'],wall_seconds=d['wall_seconds'])
        elif (HERE/(job['id']+'_upper.json')).exists():
            row['upper_checkpoint_retained']=True
        rows.append(row)
        write(HERE/'summary.json',rows)
        print(row,flush=True)
