"""Disabled acceptance pilot must exactly reproduce the frozen baseline."""
from pathlib import Path
import json, os, subprocess, sys
OUT = Path(__file__).resolve().parent
jobs = [j for j in json.loads((OUT/'verification_manifest.json').read_text())['jobs'] if j['model'] in (3,4)]
checks = []
(OUT/'disabled_logs').mkdir(exist_ok=True)
for i,job in enumerate(jobs):
    outputs = []
    for source in ('before_source','disabled_source'):
        p = subprocess.run([sys.executable,str(OUT/'fixed_work.py'),str(OUT/source),str(job['model']),job['case']],
                           capture_output=True,text=True,timeout=80,env={**os.environ,'PYTHONHASHSEED':'0'})
        (OUT/'disabled_logs'/('%s_%s.log'%(i,source))).write_text(p.stdout+'\n'+p.stderr)
        p.check_returncode();outputs.append(json.loads(p.stdout))
    a,b=outputs
    checks.append(dict(family=job['family'],model=job['model'],exact=all(a[k]==b[k] for k in ('objective','iterations','e1','e2','assignment')),
                       valid=all(r['valid'] and r['periodic'] and r['iterations']==500 for r in outputs),before=a,disabled=b))
    (OUT/'disabled_equivalence.json').write_text(json.dumps(checks,indent=2))
    print(job['family'],checks[-1]['exact'],checks[-1]['valid'],flush=True)
assert len(checks)==4 and all(r['exact'] and r['valid'] for r in checks)
