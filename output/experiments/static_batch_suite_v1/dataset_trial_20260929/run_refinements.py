"""Declared follow-up pools suggested by the completed initial scan's evidence."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT))
from experiments.runs.run_multibatch_cycle import main
out=Path(__file__).resolve().parent
specs=[('zhou_Set2_100-5-1b_subset50',[23,31]),('zhou_Set2_200-10-1',[90,100]),
       ('lade_0829_10aoi',[87]),('lade_0829_20aoi',[135]),('lade_0829_30aoi',[144])]
plan=dict(purpose='Additional declared N values only; fixed orders, windows, batches and fleet unchanged',
          basis='Initial-run necessary pool lower bounds and minimum pools of already audited schedules',
          solver='alns',seed=42,time_seconds=5,iterations=5000,
          cases=[dict(family=f,pool_sizes=p) for f,p in specs])
p=out/'refinement_plan.json'
if p.exists(): raise SystemExit('Refinement plan already exists; refusing to repeat')
p.write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
for family,pools in specs:
 main(['--dataset-manifest','data/multibatch/static_suite_v1/manifest.json','--families',family,
       '--batch-counts','3','--pool-sizes',*[str(n) for n in pools],'--solver','alns',
       '--seeds','42','--time','5','--no-plots','--output',str(out/('refine_'+family))])
