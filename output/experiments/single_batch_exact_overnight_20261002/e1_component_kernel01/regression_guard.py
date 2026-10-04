"""Wait for recorded external timing; run the required regression once."""
from pathlib import Path
import datetime
import json
import os
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[4]
OUT=Path(__file__).resolve().parent
NIGHT=OUT.parent
OTHERS=('alns_single_batch_overnight_20261001','m5_overnight_20261001','model5_alns_overnight_20261001')

def checkpoint(**fields):
    p=NIGHT/'checkpoint.json'
    data=json.loads(p.read_text());data.update(fields)
    data['updated_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')

def main():
    assert not (OUT/'regression.json').exists()
    while True:
        activity={}
        for name in OTHERS:
            d=json.loads((ROOT/'output/experiments'/name/'checkpoint.json').read_text())
            activity[name]={'status':d.get('status'),'active_process':d.get('active_process')}
        entry={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'loadavg':os.getloadavg(),'activity':activity}
        with (OUT/'regression_wait_activity.jsonl').open('a') as f:f.write(json.dumps(entry,ensure_ascii=False)+'\n')
        if not any(d['active_process'] is not None for d in activity.values()):break
        if datetime.datetime.now(datetime.timezone.utc)>=datetime.datetime(2026,10,1,23,45,tzinfo=datetime.timezone.utc):
            checkpoint(status='REQUIRED_REGRESSION_DEFERRED_EXTERNAL_TIMING',active_process=None,regression_guard_pid=None)
            return
        time.sleep(30)
    checkpoint(status='E1_KERNEL_REQUIRED_REGRESSION_RUNNING',active_process=os.getpid(),regression_guard_pid=os.getpid())
    result=subprocess.run([sys.executable,str(OUT/'regression.py')],cwd=ROOT)
    checkpoint(status='E1_KERNEL_REGRESSION_COMPLETE' if result.returncode==0 else 'E1_KERNEL_REGRESSION_FAILED',
        active_process=None,regression_guard_pid=None)
    raise SystemExit(result.returncode)

if __name__=='__main__':main()
