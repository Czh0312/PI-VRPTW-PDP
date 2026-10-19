"""Finish the already authorized experiment stages, subject to the small-case gate."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FULL = ROOT/'output/experiments/benchmark27_one_box_20261019_v1'
DATA = ROOT/'data/formal_benchmark27_one_box_v1'
BAD = {'ERROR','OUTER_TIMEOUT','INTERRUPTED','AUDIT_FAILED','DIAGNOSTIC_FAILURE','WITNESS_CONTRADICTION'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    temporary = path.with_suffix(path.suffix+'.pipeline.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temporary.replace(path)


def state(status, **details):
    write(HERE/'pipeline_state.json', dict(status=status, pid=__import__('os').getpid(),
        updated_at=datetime.now(timezone.utc).isoformat(), **details))
    print(status, flush=True)


def report():
    subprocess.run([sys.executable,str(HERE/'report.py')],cwd=ROOT,check=True)


def formal_status(status, **details):
    validation = read(HERE/'validation.json')
    validation['formal_rerun'].update(status=status, **details)
    write(HERE/'validation.json', validation)


def source_gate():
    manifest = read(HERE/'s01/manifest.json')
    for name, expected in manifest['source_hashes'].items():
        prefix = 'sources/current/'
        if not name.startswith(prefix):
            continue
        path = ROOT/name[len(prefix):]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise RuntimeError(f'Validated source changed: {path}')
    if hashlib.sha256((DATA/'manifest.json').read_bytes()).hexdigest()!=manifest['dataset_sha256']:
        raise RuntimeError('Validated dataset changed')


def main():
    state('waiting_for_representatives', study=str(HERE/'representatives'))
    prior_terminal = -1
    while True:
        progress_file = HERE/'representatives/progress.json'
        if progress_file.exists():
            try:
                progress = read(progress_file)
            except json.JSONDecodeError:
                time.sleep(2); continue
            if progress['terminal'] != prior_terminal:
                prior_terminal = progress['terminal']
                report()
                state('waiting_for_representatives', progress=progress)
            if progress['state']=='COMPLETED':
                break
        time.sleep(10)
    # The driver writes its final gate after the terminal count; wait for it.
    for _ in range(30):
        gate_path = HERE/'representatives/stage_gate.json'
        if gate_path.exists() and read(gate_path)['complete']:
            break
        time.sleep(2)
    else:
        raise RuntimeError('Representative driver has not finalized its audit gate')
    report()
    validation = read(HERE/'validation.json')
    gate = read(gate_path)
    if not validation['all_small_algorithm_acceptance'] or not gate['ready']:
        unresolved = []
        for folder in ('s01','representatives'):
            for row_path in (HERE/folder/'runs').glob('*/attempt_001/row.json'):
                row = read(row_path)
                if not row['valid']:
                    unresolved.append(dict(id=row['id'],status=row['status'],error=row.get('error')))
        formal_status('not_started_small_case_gate_not_passed', unresolved=unresolved)
        report()
        state('stopped_at_small_case_gate', unresolved=unresolved)
        return
    source_gate()
    formal_status('preparing', output=str(FULL))
    state('preparing_formal', output=str(FULL))
    if FULL.exists():
        raise RuntimeError('Formal target already exists; preserve it and review before reuse')
    subprocess.run([sys.executable,'-m','experiments.runs.run_formal_benchmark27','prepare',
        '--output',str(FULL),'--dataset',str(DATA),'--study-kind','formal','--workers','1',
        '--exact-seconds','300','--alns-seconds','30','--seeds','42','43','44','45','46',
        '--timeout-overhead','60','--overhead-basis',
        'Completed S01/S04/S07 eight-model exact/ALNS pilots and independent two-cycle audits.'],cwd=ROOT,check=True)
    manifest = read(FULL/'manifest.json')
    assert manifest['plan_count']==1296 and manifest['full_dataset']
    formal_status('running', output=str(FULL))
    state('running_formal', output=str(FULL))
    process = subprocess.Popen([sys.executable,str(FULL/'driver.py'),'run','--output',str(FULL)],cwd=ROOT)
    while process.poll() is None:
        progress_file = FULL/'progress.json'
        if progress_file.exists():
            try:
                statuses = read(progress_file)['statuses']
            except json.JSONDecodeError:
                time.sleep(2); continue
            failures = {k:v for k,v in statuses.items() if k in BAD and v}
            if failures and not (FULL/'STOP').exists():
                (FULL/'STOP').write_text('Pipeline stops after execution/audit error: '+json.dumps(failures)+'\n')
        time.sleep(5)
    if process.returncode:
        raise RuntimeError(f'Formal driver returned {process.returncode}')
    gate = read(FULL/'stage_gate.json')
    final = 'completed' if gate['ready'] else 'stopped_or_completed_with_errors'
    formal_status(final, output=str(FULL), gate=gate, progress=read(FULL/'progress.json'))
    report()
    state(final, output=str(FULL), gate=gate)


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        formal_status('pipeline_error', error=str(exc))
        state('pipeline_error', error=str(exc))
        raise
