"""One resumable pipeline; waits for active/running metadata, no duplicate work."""
from datetime import datetime,timezone
import os
import subprocess
import sys
from common import OUT,NIGHT,read,write,gate


def main():
    cp=read(NIGHT/'checkpoint.json')
    cp.update(balance_pipeline_pid=os.getpid(),balance_pipeline_status='REGISTERED_SINGLE_PIPELINE',
              active_process=None,updated_utc=datetime.now(timezone.utc).isoformat())
    write(NIGHT/'checkpoint.json',cp)
    if not (OUT/'validation.json').exists():
        gate('independent tiny and original projections')
        cp=read(NIGHT/'checkpoint.json');cp.update(active_process=dict(kind='independent_tiny_price_balance_validation',pid=os.getpid()))
        write(NIGHT/'checkpoint.json',cp)
        with (OUT/'validation.log').open('x') as f:
            run=subprocess.run([sys.executable,str(OUT/'validate.py')],cwd=OUT,stdout=f,stderr=subprocess.STDOUT,timeout=120)
        assert run.returncode==0,'Retain validation failure before timing; do not blindly restart'
    if not (OUT/'prices5/comparison.json').exists() or len(read(OUT/'prices5/comparison.json'))<16:
        with (OUT/'conductor.log').open('a') as f:
            run=subprocess.run([sys.executable,str(OUT/'compare.py')],cwd=OUT,stdout=f,stderr=subprocess.STDOUT)
        assert run.returncode==0,'Retain comparison failure without retry'
    cp=read(NIGHT/'checkpoint.json');cp.update(active_process=None,balance_pipeline_status='COMPARISONS_COMPLETE',balance_pipeline_pid=None)
    write(NIGHT/'checkpoint.json',cp)


if __name__=='__main__':main()
