"""The adopted source and live workspace must produce identical fixed-work outputs."""
from pathlib import Path
import json,os,subprocess,sys
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2]
source=sys.argv[1] if len(sys.argv)>1 else 'adopted_source'
cases=[OUT/'m3_benchmark_inputs/zhou_Set2_100-5-2/case.json',OUT/'m3_benchmark_inputs/zhou_Set2_100-10-1/case.json']
checks=[]
for model in range(1,5):
 for case in cases:
  outputs=[]
  for base in (OUT/source,ROOT):
   p=subprocess.run([sys.executable,str(OUT/'m3_fixed_worker.py'),str(base),str(model),str(case)],capture_output=True,text=True,check=True,timeout=80,env={**os.environ,'PYTHONHASHSEED':'0'})
   outputs.append(json.loads(p.stdout))
  a,b=outputs;same=all(a[k]==b[k] for k in ('objective','iterations','e1','e2','assignment'))
  checks.append(dict(model=model,family=case.parent.name,identical=same,valid=a['valid'] and b['valid'] and a['periodic'] and b['periodic'],source=source,frozen=a,live=b))
  (OUT/'live_equivalence.json').write_text(json.dumps(checks,indent=2))
  print(model,case.parent.name,same,a['valid'],b['valid'],flush=True)
assert len(checks)==8 and all(c['identical'] and c['valid'] for c in checks)
