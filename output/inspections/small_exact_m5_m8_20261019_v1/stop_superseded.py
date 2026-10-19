"""Stop only this study's superseded controller and out-of-scope workers."""
import json
import os
from pathlib import Path
import psutil

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
SCRIPT = OUT / 'run_probe.py'


def same_path(value, target):
    try:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        return candidate.resolve() == target
    except (OSError, ValueError):
        return False


def matches(kind):
    found = []
    for process in psutil.process_iter(['pid', 'name', 'cmdline']):
        if process.pid == os.getpid() or not (process.info['name'] or '').lower().startswith('python'):
            continue
        command = process.info['cmdline'] or []
        if kind == 'controller' and any(same_path(arg, SCRIPT) for arg in command[1:]):
            found.append(process)
        if kind == 'worker' and '--output' in command:
            index = command.index('--output') + 1
            if index < len(command):
                path = Path(command[index]).resolve()
                if path.parent == OUT / 'runs' and not path.name.startswith(('S01_', 'S04_', 'S07_')):
                    found.append(process)
    return found


stopped = []
for kind in ('controller', 'worker'):
    selected = matches(kind)
    for process in selected:
        try:
            process.terminate()
            stopped.append(dict(kind=kind, pid=process.pid))
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(selected, timeout=5)
    for process in alive:
        process.kill()
print(json.dumps(dict(stopped=stopped, completed_results_preserved=True)))
