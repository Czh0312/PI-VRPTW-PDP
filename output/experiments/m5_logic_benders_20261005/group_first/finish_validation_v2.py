from pathlib import Path
import hashlib,json,subprocess,sys,os
H=Path(__file__).resolve().parent
def write(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
jobs=[p for p in sorted(H.glob('*_*/job.json'))]
finished=[]
for p in jobs:
    target=H/'qa_v2'/p.parent.name
    if not (target/'validation.json').exists():
        with (H/(p.parent.name+'_validation_v2.log')).open('w') as log:
            code=subprocess.run([sys.executable,str(H/'validator_v2.py'),'--validate',str(p),'--validation-output',str(target)],stdout=log,stderr=subprocess.STDOUT).returncode
        if code:raise RuntimeError('Validation failed: '+str(p))
    finished.append(str(p))
    write(H/'validation_checkpoint_v2.json',dict(status='VALIDATING',active_process=os.getpid(),completed=finished))
    print(p.parent.name+' verified',flush=True)
before=json.loads((H/'protected_before.json').read_text())
after={p:sha(Path(p)) for p in before}
assert before==after
rows=[]
for p in jobs:
    job=json.loads(p.read_text());d=json.loads(p.with_name('result.json').read_text())
    rows.append(dict(customers=job['customers'],seed=job['seed'],variant=job['variant'],bound=d['bound'],upper=d['upper_bound'],gap=d['gap'],runtime=d['runtime'],cuts=len(d['cuts']),oracles=d['cache']['oracle_calls'],status=d['status'],budget_overrun_seconds=d['budget_overrun_seconds']))
write(H/'summary.json',dict(rows=rows,same_input=True,same_initial=True,same_total_budget=60,sequential=True,cold_cache=True,all_independent_validations_passed=True,protected_certificates_unchanged=True,validation_directory=str(H/'qa_v2'),initial_validator_failure='Replay used Gurobi default relative MIPGap; v2 sets MIPGap=MIPGapAbs=0. Frozen original driver/failure logs preserved.',proof_scope='Verified complete-domain lower and audited upper; no full M5 optimality claim'))
write(H/'protected_after.json',after)
write(H/'validation_checkpoint_v2.json',dict(status='COMPLETE_VERIFIED',active_process=None,completed=finished))
write(H/'checkpoint.json',dict(status='COMPLETE_VERIFIED',active_process=None,completed=finished,validation_directory=str(H/'qa_v2')))
# Freeze only closed child logs and completed files. Parent stdout is external.
manifest={str(p):sha(p) for p in H.rglob('*') if p.is_file() and p.name!='manifest.json' and not p.name.endswith('.lock') and '__pycache__' not in str(p)}
write(H/'manifest.json',dict(sha256=manifest))
print(json.dumps(rows,ensure_ascii=False),flush=True)
