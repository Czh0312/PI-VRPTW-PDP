"""Check the stage split on all large instances, with reversed block order."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'output/experiments/solver_optimization_20260913'


def main():
    while json.loads((OUT/'adopted_progress.json').read_text())['status'] != 'complete':
        time.sleep(10)
    for seed, ratios in [(42,[.8,.9]), (43,[.9,.8])]:
        for ratio in ratios:
            name = f'm6_ratio{round(ratio*100)}_seed{seed}'
            dest = OUT/name
            dest.mkdir(exist_ok=True)
            if (dest/'COMPLETE').exists():
                continue
            (OUT/'m6_ratio_progress.json').write_text(json.dumps(dict(status='running',stage=name)),encoding='utf-8')
            options = dict(stage1_time_ratio=ratio,transshipment_time_budget=1.,
                transshipment_sample_pickups=12,transshipment_sample_locations=6,
                transshipment_sample_carriers=4,transshipment_position_top_k=4)
            args = [sys.executable,'experiments/runs/optimize_solvers_20260913.py',
                '--output',str(dest.relative_to(ROOT)),'--source-root',str(OUT/'adopted_source'),
                '--method','alns','--scale','large','--models','6','--seconds','5',
                '--seeds',str(seed),'--options',json.dumps(options)]
            print('START',name,flush=True)
            with (dest/'console.log').open('a',encoding='utf-8') as log:
                subprocess.run(args,cwd=ROOT,env={**os.environ,'PYTHONHASHSEED':'0'},
                    stdout=log,stderr=subprocess.STDOUT,check=True)
            rows = [json.loads(s) for s in (dest/'runs.jsonl').read_text(encoding='utf-8').splitlines()]
            assert len(rows)==18 and not any(r.get('error') for r in rows)
            assert all(r['valid'] for r in rows if r['objective'] is not None)
            (dest/'COMPLETE').write_text(str(time.time()),encoding='utf-8')
            print('COMPLETE',name,flush=True)
    (OUT/'m6_ratio_progress.json').write_text(json.dumps(dict(status='complete')),encoding='utf-8')
    print('M6 FULL PAIRED CHECK COMPLETE',flush=True)


if __name__=='__main__':
    main()
