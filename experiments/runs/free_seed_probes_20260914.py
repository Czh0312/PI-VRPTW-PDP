"""Isolate initial-assignment polishing from stage allocation on difficult cases."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'output/experiments/solver_optimization_20260913'


def main():
    while True:
        p = OUT/'post_progress.json'
        if p.exists() and json.loads(p.read_text()).get('status')=='complete':
            break
        time.sleep(10)
    cases = ['Set1_7-2-3','Set1_10-2-2b','Set1_12-2-3','Set1_12-2-4']
    relay = dict(transshipment_time_budget=1.25,transshipment_sample_pickups=12,
        transshipment_sample_locations=6,transshipment_sample_carriers=4,transshipment_position_top_k=4)
    probes = [
        ('m4_seed20_small',4,dict(seed_search_fraction=.2)),
        ('m7_seed20_small',7,dict(seed_search_fraction=.2,**relay)),
        ('m7_stage90_small',7,dict(stage1_time_ratio=.9,**relay)),
        ('m7_seed20_stage90_small',7,dict(seed_search_fraction=.2,stage1_time_ratio=.9,**relay)),
    ]
    for name,model,options in probes:
        (OUT/'free_seed_progress.json').write_text(json.dumps(dict(stage=name,status='running')),encoding='utf-8')
        print('START',name,flush=True)
        with (OUT/f'{name}.log').open('w',encoding='utf-8') as log:
            subprocess.run([sys.executable,'experiments/runs/optimize_solvers_20260913.py',
                '--output',str((OUT/name).relative_to(ROOT)), '--source-root',str(OUT/'final_source'),
                '--method','alns','--models',str(model),'--seconds','5',
                '--instances',*cases,'--options',json.dumps(options)],cwd=ROOT,
                env={**os.environ,'PYTHONHASHSEED':'0'},stdout=log,stderr=subprocess.STDOUT,check=True)
        print('COMPLETE',name,flush=True)
    (OUT/'free_seed_progress.json').write_text(json.dumps(dict(status='complete')),encoding='utf-8')
    print('FREE SEED PROBES COMPLETE',flush=True)


if __name__=='__main__':
    main()
