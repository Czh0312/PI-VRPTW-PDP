"""Serial, resumable final validation using one immutable candidate source tree."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'output/experiments/solver_optimization_20260913'
RUNNER = ROOT/'experiments/runs/optimize_solvers_20260913.py'
SOURCE = OUT/'final_source'


def main():
    if not (SOURCE/'models').exists():
        shutil.copytree(ROOT/'models',SOURCE/'models',ignore=shutil.ignore_patterns('__pycache__'))
    env = {**os.environ,'PYTHONHASHSEED':'0'}
    selected = ['Set1_7-2-3','Set1_10-2-2b','Set1_12-2-3','Set1_12-2-4']
    large = ['Set2_100-5-1','Set2_100-10-1','Set2_200-10-1']
    source_args = ['--source-root',str(SOURCE.relative_to(ROOT))]
    stages = [
        ('control_baseline_exact',['--baseline','--method','exact','--seconds','15','--instances',*selected]),
        ('control_current_exact',[*source_args,'--method','exact','--seconds','15','--instances',*selected]),
        ('control_baseline_small',['--baseline','--method','alns','--seconds','3','--instances',*selected]),
        ('control_current_small',[*source_args,'--method','alns','--seconds','3','--instances',*selected]),
        ('control_baseline_large',['--baseline','--method','alns','--scale','large','--seconds','5','--seeds','42','43','--instances',*large]),
        ('control_current_large',[*source_args,'--method','alns','--scale','large','--seconds','5','--seeds','42','43','--instances',*large]),
        ('final_exact',[*source_args,'--method','exact','--seconds','15']),
        ('final_small_alns',[*source_args,'--method','alns','--seconds','5']),
        ('final_large_alns',[*source_args,'--method','alns','--scale','large','--seconds','5','--seeds','42']),
    ]
    def run(name,args):
        folder = OUT/name
        folder.mkdir(exist_ok=True)
        completed = folder/'COMPLETE'
        if completed.exists():
            return
        progress = dict(stage=name,started=time.time(),status='running',source=str(SOURCE))
        (OUT/'campaign_progress.json').write_text(json.dumps(progress,indent=2),encoding='utf-8')
        print('START',name,flush=True)
        with (folder/'console.log').open('a',encoding='utf-8') as log:
            result = subprocess.run([sys.executable,str(RUNNER),'--output',str(folder.relative_to(ROOT)),*args],
                                    cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:
            progress.update(status='failed',returncode=result.returncode)
            (OUT/'campaign_progress.json').write_text(json.dumps(progress,indent=2),encoding='utf-8')
            raise RuntimeError(f'{name} failed; inspect {folder / "console.log"}')
        completed.write_text(str(time.time()),encoding='utf-8')
        print('COMPLETE',name,flush=True)
    for name,args in stages:
        run(name,args)
    rows = [json.loads(s) for s in (OUT/'final_exact/runs.jsonl').read_text(encoding='utf-8').splitlines()]
    for model in (1,2,3,4,6,7):
        pending = [r['instance'] for r in rows if r['model']==model and not r['proven_optimal']]
        if pending:
            run(f'proof_extension_m{model}',[*source_args,'--method','exact','--seconds','90',
                '--models',str(model),'--instances',*pending])
    (OUT/'campaign_progress.json').write_text(json.dumps(dict(status='complete',finished=time.time()),indent=2),encoding='utf-8')
    print('CAMPAIGN COMPLETE',flush=True)


if __name__ == '__main__':
    main()
