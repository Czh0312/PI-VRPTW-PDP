"""Recover recording failure; retain initial logs/inputs, no timing retry."""
from datetime import datetime,timezone
import os
import subprocess
import sys
import traceback
from common import OUT,NIGHT,read,write,gate


def main():
    cp=read(NIGHT/'checkpoint.json')
    assert cp.get('active_process') is None,'Do not duplicate existing solver work'
    cp.update(balance_pipeline_pid=os.getpid(),balance_pipeline_status='RECOVERY_V2_SINGLE_PIPELINE',
              active_process=None,balance_pipeline_script=str(OUT/'control_v2.py'),
              updated_utc=datetime.now(timezone.utc).isoformat())
    write(NIGHT/'checkpoint.json',cp)
    try:
        if not (OUT/'validation.json').exists():
            assert not (OUT/'validation_v2.log').exists(),'Retain failed validation; no blind retry'
            gate('validation_v2 complete independent prices/original projections')
            cp=read(NIGHT/'checkpoint.json');cp.update(status='M4_PRICE_BALANCE_VALIDATION_V2_RUNNING',
                active_process=dict(kind='independent_tiny_price_balance_validation_v2',pipeline_pid=os.getpid()),
                balance_waiting_pid=None,balance_waiting_stage=None)
            write(NIGHT/'checkpoint.json',cp)
            with (OUT/'validation_v2.log').open('x') as f:
                run=subprocess.run([sys.executable,str(OUT/'validate_v2.py')],cwd=OUT,stdout=f,stderr=subprocess.STDOUT,timeout=120)
            assert run.returncode==0,'Validation_v2 failed; all logs retained before timing'
        cp=read(NIGHT/'checkpoint.json');cp.update(active_process=None,balance_validation_complete=True)
        write(NIGHT/'checkpoint.json',cp)
        if not (OUT/'prices5/comparison.json').exists() or len(read(OUT/'prices5/comparison.json'))<16:
            with (OUT/'conductor.log').open('a') as f:
                run=subprocess.run([sys.executable,str(OUT/'compare.py')],cwd=OUT,stdout=f,stderr=subprocess.STDOUT)
            assert run.returncode==0,'Fresh16 pricing comparison interrupted/failed; retain all rows, never repeat completed/failed rows'
        cp=read(NIGHT/'checkpoint.json');cp.update(status='M4_PRICE_BALANCE_FIXED16_COMPLETE',
            active_process=None,balance_pipeline_status='COMPARISONS_COMPLETE',balance_pipeline_pid=None,
            balance_waiting_pid=None,balance_waiting_stage=None,
            next_action='Run verify.py once after all16 rows; evaluate all8pairs with original prices/starts/TOTALbudgets. No default adoption or full-integer speed claim.')
        write(NIGHT/'checkpoint.json',cp)
    except BaseException as error:
        cutoff=datetime.now(timezone.utc)>=datetime.fromisoformat('2026-10-01T23:00:00+00:00')
        write(OUT/'recovery_v2_outcome.json',dict(recorded_utc=datetime.now(timezone.utc).isoformat(),
            status='CUTOFF_AFTER0700_SAVE_PARTIAL' if cutoff else 'PIPELINE_FAILURE_RETAINED',
            error=repr(error),traceback=traceback.format_exc(),retry_timing=False))
        cp=read(NIGHT/'checkpoint.json');cp.update(active_process=None,balance_pipeline_pid=None,
            balance_waiting_pid=None,balance_waiting_stage=None,
            balance_pipeline_status='CUTOFF' if cutoff else 'RECOVERY_V2_FAILED',
            status='M4_PRICE_BALANCE_CUTOFF_SAVED' if cutoff else 'M4_PRICE_BALANCE_RECOVERY_V2_FAILURE_RETAINED')
        write(NIGHT/'checkpoint.json',cp)
        raise


if __name__=='__main__':main()
