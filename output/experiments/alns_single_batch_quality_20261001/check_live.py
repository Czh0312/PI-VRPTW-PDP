"""Live integration must reproduce the frozen fixed-iteration outputs."""
from pathlib import Path
import os,json,subprocess,sys
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2]
rows=json.loads((OUT/'fixed_work_runs.json').read_text());checks=[]
cases={'zhou_Set2_100-5-1b':ROOT/'output/experiments/alns_large_status_20260930/benchmark_inputs/zhou_Set2_100-5-1b/case.json',
 'zhou_Set2_100-10-1':ROOT/'output/experiments/alns_single_batch_descent_20260930/benchmark_inputs/zhou_Set2_100-10-1/case.json'}
for model in range(1,5):
 for family,case in cases.items():
  expected=next(r for r in rows if r['version']=='after_source' and r['model']==model and r['family']==family)
  proc=subprocess.run([sys.executable,str(OUT/'fixed_work.py'),str(ROOT),str(model),str(case)],capture_output=True,
   text=True,env={**os.environ,'PYTHONHASHSEED':'0'},check=True,timeout=80)
  actual=json.loads(proc.stdout)
  same=all(actual[k]==expected[k] for k in ('objective','iterations','e1','e2','assignment'))
  checks.append(dict(model=model,family=family,identical=same,valid=actual['valid'],actual=actual))
  (OUT/'live_equivalence.json').write_text(json.dumps(checks,indent=2))
  print(model,family,'identical',same,'valid',actual['valid'],flush=True)
assert all(c['identical'] and c['valid'] for c in checks)
