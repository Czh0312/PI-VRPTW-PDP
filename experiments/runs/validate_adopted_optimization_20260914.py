"""Freeze final policies and rerun every model/scale whose search changed."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'output/experiments/solver_optimization_20260913'
SOURCE = OUT/'adopted_source'


def main():
    progress_path = OUT/'adopted_progress.json'
    if progress_path.exists() and json.loads(progress_path.read_text())['status']=='complete':
        print('ADOPTED VALIDATION ALREADY COMPLETE; frozen batches retained',flush=True)
        return
    if not (SOURCE/'models').exists():
        shutil.copytree(ROOT/'models',SOURCE/'models',ignore=shutil.ignore_patterns('__pycache__'))
        for relative in ['cli/route.py','config/algorithms/alns_default.json']:
            dest = SOURCE/relative
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/relative,dest)
    def run(name,method,models,scale='small',options=None,seeds=None,instances=None,seconds=5):
        dest = OUT/name
        dest.mkdir(exist_ok=True)
        if (dest/'COMPLETE').exists():
            return
        (OUT/'adopted_progress.json').write_text(json.dumps(dict(stage=name,status='running',started=time.time())),encoding='utf-8')
        cmd = [sys.executable,'experiments/runs/optimize_solvers_20260913.py',
            '--output',str(dest.relative_to(ROOT)),'--source-root',str(SOURCE),
            '--method',method,'--models',*[str(m) for m in models],
            '--scale',scale,'--seconds',str(seconds),'--options',json.dumps(options or {})]
        if seeds:
            cmd += ['--seeds',*[str(s) for s in seeds]]
        if instances:
            cmd += ['--instances',*instances]
        print('START',name,flush=True)
        with (dest/'console.log').open('a',encoding='utf-8') as log:
            subprocess.run(cmd,cwd=ROOT,env={**os.environ,'PYTHONHASHSEED':'0'},
                           stdout=log,stderr=subprocess.STDOUT,check=True)
        rows = [json.loads(s) for s in (dest/'runs.jsonl').read_text(encoding='utf-8').splitlines()]
        assert not any(r.get('error') for r in rows), f'Run exception: {name}'
        assert all(r['valid'] for r in rows if r['objective'] is not None), f'Invalid route: {name}'
        (dest/'COMPLETE').write_text(str(time.time()),encoding='utf-8')
        print('COMPLETE',name,flush=True)
    relay = dict(stage1_time_ratio=None,transshipment_sample_pickups=12,
                 transshipment_sample_locations=6,transshipment_sample_carriers=4,
                 transshipment_position_top_k=4)
    run('adopted_exact_m7','exact',[7],seconds=15,seeds=[42])
    run('adopted_large_m2','alns',[2],scale='large',seeds=[42])
    run('adopted_large_m6','alns',[6],scale='large',seeds=[42],
        options=dict(**relay,transshipment_time_budget=1.))
    run('adopted_large_m7','alns',[7],scale='large',seeds=[42],
        options=dict(**relay,transshipment_time_budget=1.25))
    run('adopted_small_m4','alns',[4])
    run('adopted_small_m7','alns',[7],options=dict(**relay,transshipment_time_budget=1.25))
    rows = [json.loads(s) for s in (OUT/'adopted_exact_m7/runs.jsonl').read_text(encoding='utf-8').splitlines()]
    pending = [r['instance'] for r in rows if not r['proven_optimal']]
    if pending:
        run('adopted_m7_proof90','exact',[7],seconds=90,seeds=[42],instances=pending)
    for path in (SOURCE/'models').rglob('*.py'):
        assert path.read_bytes()==(ROOT/path.relative_to(SOURCE)).read_bytes(), 'Working solver source changed during final validation'
    (OUT/'adopted_progress.json').write_text(json.dumps(dict(status='complete',finished=time.time())),encoding='utf-8')
    print('ADOPTED VALIDATION COMPLETE',flush=True)


if __name__=='__main__':
    main()
