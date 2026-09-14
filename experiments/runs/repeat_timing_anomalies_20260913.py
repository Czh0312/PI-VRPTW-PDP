"""Repeat severely over-budget observations without overwriting original runs."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'output/experiments/solver_optimization_20260913'


def main():
    assert json.loads((OUT/'campaign_progress.json').read_text())['status']=='complete', 'Wait for serial campaign'
    for path in sorted(OUT.glob('*/runs.jsonl')):
        if not path.parent.name.startswith(('control_','final_','proof_extension_')):
            continue
        manifest = json.loads((path.parent/'manifest.json').read_text(encoding='utf-8'))
        args = manifest['arguments']
        for r in map(json.loads,path.read_text(encoding='utf-8').splitlines()):
            # Only obvious interruptions: do not hide ordinary initialization
            # overhead, failed solves, or difficult but normally timed runs.
            if r['wall_time'] <= max(60,10*r['budget']):
                continue
            name = f"{r['instance']}_M{r['model']}_S{r['seed']}"
            dest = OUT/'timing_repeat'/path.parent.name/name
            cmd = [sys.executable,str(ROOT/'experiments/runs/optimize_solvers_20260913.py'),
                '--output',str(dest.relative_to(ROOT)), '--source-root',manifest['solver_source'],
                '--method',r['method'],'--models',str(r['model']),'--instances',r['instance'],
                '--seconds',str(r['budget']),'--seeds',str(r['seed']),
                '--scale',r['scale'],'--scenario',r['scenario'],'--options',args['options']]
            if args['baseline']:
                cmd.append('--baseline')
            print('REPEAT',path.parent.name,name,flush=True)
            subprocess.run(cmd,cwd=ROOT,env={**os.environ,'PYTHONHASHSEED':'0'},check=True)
    print('TIMING REPEATS COMPLETE',flush=True)


if __name__=='__main__':
    main()
