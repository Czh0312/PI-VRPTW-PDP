"""An explicitly disabled M2 proposal reproduces the frozen baseline exactly."""
import json, os, subprocess, sys
from pathlib import Path

out = Path(__file__).resolve().parent
root = out.parents[2]
jobs = [j for j in json.loads((out/'verification_manifest.json').read_text())['jobs'] if j['model'] == 2]
checks = []
for job in jobs:
    outputs = []
    for source, mode in [(out/'before_source',[]), (root,['disabled'])]:
        process = subprocess.run([sys.executable,str(out/'fixed_work.py'),str(source),'2',job['case'],*mode],
                                 capture_output=True,text=True,check=True,timeout=80,
                                 env={**os.environ,'PYTHONHASHSEED':'0'})
        outputs.append(json.loads(process.stdout))
    same = all(outputs[0][k] == outputs[1][k] for k in ('objective','iterations','e1','e2','assignment'))
    valid = all(r['valid'] and r['periodic'] and r['iterations'] == 500 for r in outputs)
    checks.append(dict(family=job['family'],identical=same,valid=valid,baseline=outputs[0],disabled=outputs[1]))
    (out/'disabled_equivalence.json').write_text(json.dumps(checks,indent=2))
    print(job['family'],same,valid,flush=True)
assert len(checks) == 2 and all(r['identical'] and r['valid'] for r in checks)
