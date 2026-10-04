"""Baseline, adopted source and live public M1-M4 must reproduce exactly."""
from pathlib import Path
import json,os,subprocess,sys
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2]
source=sys.argv[1] if len(sys.argv)>1 else 'adopted_source'
jobs=json.loads((OUT/'verification_manifest.json').read_text())['jobs'];checks=[]
for job in jobs:
 outputs=[]
 bases=[OUT/'before_source',OUT/source,ROOT]
 if source=='adopted_source' and job['model']==2:bases.append(OUT/source)
 for base in bases:
  p=subprocess.run([sys.executable,str(OUT/'fixed_work.py'),str(base),str(job['model']),job['case']],capture_output=True,text=True,check=True,timeout=80,env={**os.environ,'PYTHONHASHSEED':'0'})
  outputs.append(json.loads(p.stdout))
 baseline,frozen,live=outputs[:3]
 reference=frozen if source=='adopted_source' and job['model']==2 else baseline
 same=all(all(r[k]==reference[k] for k in ('objective','iterations','e1','e2','assignment')) for r in outputs[1:])
 valid=all(r['valid'] and r['periodic'] and r['iterations']==500 for r in outputs)
 checks.append(dict(model=job['model'],family=job['family'],identical=same,valid=valid,source=source,baseline=baseline,frozen=frozen,live=live))
 (OUT/'live_equivalence.json').write_text(json.dumps(checks,indent=2));print(job['model'],job['family'],same,valid,flush=True)
assert len(checks)==8 and all(r['identical'] and r['valid'] for r in checks)
