"""One serial external-load-gated targeted validation/preparation/comparison."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[4];OUT=Path(__file__).resolve().parent;NIGHT=OUT.parent
NAMES=('alns_single_batch_overnight_20261001','m5_overnight_20261001','model5_alns_overnight_20261001')
OWNED=('models/routing/solvers/exact/single_batch_e1_bound.py','models/routing/solvers/exact/solver.py',
    'tests/routing/test_single_batch_e1_bound.py','tests/routing/test_single_batch_e1_kernel.py',
    'tests/routing/test_single_batch_e1_clocked.py')

def write(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def checkpoint(status,active=None,**fields):
    p=NIGHT/'checkpoint.json';d=json.loads(p.read_text());d.update(status=status,active_process=active,
        updated_utc=datetime.now(timezone.utc).isoformat(),**fields);write(p,d)

def hashes():
    return {str(p.relative_to(ROOT)):sha(p) for folder in ('models','configuration','tests')
        for p in sorted((ROOT/folder).rglob('*.py'))}

def gate(stage):
    while True:
        activity={}
        for name in NAMES:
            data=json.loads((ROOT/'output/experiments'/name/'checkpoint.json').read_text())
            activity[name]={k:data.get(k) for k in ('status','active_process')}
        entry={'utc':datetime.now(timezone.utc).isoformat(),'stage':stage,'loadavg':os.getloadavg(),'activity':activity}
        with (OUT/'wait_activity.jsonl').open('a') as f:f.write(json.dumps(entry,ensure_ascii=False)+'\n')
        if datetime.now(timezone.utc)>=datetime(2026,10,1,23,0,tzinfo=timezone.utc):
            checkpoint('CLOCKED_E1_MORNING_STOP',next_action='No new large experiments after07:00; retain incomplete plan and wrapup report.')
            return False
        if not any(d['active_process'] is not None for d in activity.values()):return True
        checkpoint('CLOCKED_E1_PREFLIGHT_WAITING_EXTERNAL',None,clocked_pipeline_pid=os.getpid(),clocked_pipeline_wait_stage=stage,
            next_action='Existing single pipeline waits external activity; do not duplicate tests/preparation or comparisons.')
        time.sleep(30)

def run_capture(args,name):
    path=OUT/name;assert not path.exists()
    with path.open('w') as f:
        result=subprocess.run([sys.executable,*args],cwd=ROOT,stdout=f,stderr=subprocess.STDOUT)
    return result.returncode

def main():
    assert not (OUT/'targeted_tests.log').exists() and not (OUT/'full15_30').exists()
    if not gate('targeted correctness tests'):return
    before=hashes();snapshot=OUT/'validation_source_snapshot';snapshot.mkdir()
    for name,h in before.items():
        target=snapshot/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,target);assert sha(target)==h
    checkpoint('CLOCKED_E1_TARGETED_VALIDATION_RUNNING',dict(pipeline_pid=os.getpid(),stage='targeted validation'))
    code=run_capture(['-m','pytest','-q','tests/routing/test_single_batch_e1_clocked.py',
        'tests/routing/test_single_batch_e1_bound.py','tests/routing/test_single_batch_e1_kernel.py'],'targeted_tests.log')
    after=hashes();match=re.search(r'(\d+) passed(?:, (\d+) warnings)? in ([\d.]+)s',(OUT/'targeted_tests.log').read_text())
    record=dict(exit_code=code,passed=int(match.group(1)) if match else None,
        seconds=float(match.group(3)) if match else None,before_sha256=before,after_sha256=after,
        changed_files=[p for p in set(before)|set(after) if before.get(p)!=after.get(p)],
        owned_unchanged={p:before[p]==after[p] for p in OWNED})
    write(OUT/'validation_manifest.json',record)
    if code or not all(record['owned_unchanged'].values()):
        checkpoint('CLOCKED_E1_TARGETED_VALIDATION_FAILED',next_action='Failure preserved; independently diagnose before a new validation version. No comparison launched.')
        raise SystemExit(code or 1)
    checkpoint('CLOCKED_E1_TARGETED_VALIDATION_PASSED',dict(pipeline_pid=os.getpid(),stage='prepare'),clocked_e1_targeted_tests={'passed':record['passed'],'seconds':record['seconds']})
    if not gate('original schedule/actual row projection and freezing'):return
    code=run_capture([str(OUT/'compare.py'),'--prepare'],'prepare.log')
    if code:
        checkpoint('CLOCKED_E1_PREPARATION_FAILED',next_action='Preparation failure preserved; no performance comparison executed.')
        raise SystemExit(code)
    checkpoint('CLOCKED_E1_KERNEL_PREPARED',dict(pipeline_pid=os.getpid(),stage='comparisons'),current_driver=str(OUT/'compare.py'))
    code=run_capture([str(OUT/'compare.py'),'--run'],'conductor.log')
    if code:
        checkpoint('CLOCKED_E1_CONDUCTOR_FAILED',next_action='Retain all completed/failed rows; diagnose existing conductor output, no favorable reruns.')
        raise SystemExit(code)
    checkpoint('CLOCKED_E1_PIPELINE_COMPARISONS_COMPLETE',next_action='Existing24job comparison complete; independently audit original schedules and clocked certificates, then required full regression. DefaultsFalse; no automatic adoption.')

if __name__=='__main__':main()
