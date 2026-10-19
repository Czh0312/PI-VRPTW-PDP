"""Pause only this study's known controllers/workers; preserve all result files."""
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sys
import psutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FULL = ROOT/'output/experiments/benchmark27_one_box_20261019_v1'
sys.path.insert(0,str(ROOT))


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    for path in (HERE/'PIPELINE_STOP', HERE/'representatives/STOP'):
        path.write_text('User requested pause.\n',encoding='utf-8')
    if FULL.exists():
        (FULL/'STOP').write_text('User requested pause.\n',encoding='utf-8')
    scripts={str((HERE/name).resolve()).casefold() for name in ('pilot.py','finish_pipeline.py')}
    scripts.update(str((folder/'driver.py').resolve()).casefold() for folder in (HERE/'representatives',FULL))
    targets={}
    for process in psutil.process_iter(['pid','cmdline']):
        if process.pid==os.getpid():
            continue
        try:
            arguments=process.info['cmdline'] or []
            if not any(str(Path(a).resolve()).casefold() in scripts for a in arguments if a.endswith('.py')):
                continue
            targets[process.pid]=process
            for child in process.children(recursive=True):
                targets[child.pid]=child
        except (psutil.NoSuchProcess,psutil.AccessDenied):
            continue
    stopped=[]
    for pid,process in targets.items():
        try:
            command=process.cmdline()
            process.terminate()
            stopped.append(dict(pid=pid,command=command))
        except psutil.NoSuchProcess:
            pass
    _,alive=psutil.wait_procs(list(targets.values()),timeout=5)
    for process in alive:
        process.kill()
    psutil.wait_procs(alive,timeout=5)
    # Let the existing driver finalize interrupted attempts, then stop its
    # queue immediately via STOP. It will not launch any new solver calls.
    import subprocess
    for folder in (HERE/'representatives',FULL):
        if (folder/'manifest.json').exists():
            subprocess.run([sys.executable,str(folder/'driver.py'),'run','--output',str(folder)],cwd=ROOT,check=True)
    validation=read(HERE/'validation.json')
    validation['formal_rerun']['status']='paused_by_user'
    validation['user_pause']=dict(at=datetime.now(timezone.utc).isoformat(),stopped_processes=stopped)
    write(HERE/'validation.json',validation)
    write(HERE/'pipeline_state.json',dict(status='paused_by_user',updated_at=datetime.now(timezone.utc).isoformat(),stopped_processes=stopped))
    subprocess.run([sys.executable,str(HERE/'report.py')],cwd=ROOT,check=True)
    write(HERE/'pause_record.json',dict(paused=True,stopped=stopped,
        representatives=read(HERE/'representatives/progress.json'),full_started=FULL.exists()))
    print(json.dumps(dict(paused=True,stopped_pids=[p['pid'] for p in stopped],
        representatives=read(HERE/'representatives/progress.json'),full_started=FULL.exists()),ensure_ascii=False))


if __name__=='__main__':
    main()
