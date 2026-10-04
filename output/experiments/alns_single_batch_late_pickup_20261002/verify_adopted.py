"""Validate frozen candidate/live outputs; baseline equality applies outside M3.

This is an acceptance-policy change. Enabled M3 is expected to differ from the
old baseline; disabled M3 equality was required before quality. No timing rerun.
"""
from pathlib import Path
import json, os, subprocess, sys
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
jobs=json.loads((OUT/'verification_manifest.json').read_text())['jobs']
checks=[]
(OUT/'public_logs').mkdir(exist_ok=True)
fields=('objective','iterations','e1','e2','assignment')
for i,job in enumerate(jobs):
    rows=[]
    for label,source in [('baseline',OUT/'before_source'),('candidate',OUT/'after_source'),('live',ROOT)]:
        p=subprocess.run([sys.executable,str(OUT/'fixed_work.py'),str(source),str(job['model']),job['case']],
                         capture_output=True,text=True,timeout=80,env={**os.environ,'PYTHONHASHSEED':'0'})
        (OUT/'public_logs'/('%s_%s.log'%(i,label))).write_text(p.stdout+'\n'+p.stderr)
        p.check_returncode();rows.append(json.loads(p.stdout))
    a,b,c=rows
    candidate_exact=all(b[k]==c[k] for k in fields)
    baseline_equal=all(a[k]==b[k] for k in fields)
    valid=all(r['valid'] and r['periodic'] and r['iterations']==500 for r in rows)
    checks.append(dict(model=job['model'],family=job['family'],candidate_live_exact=candidate_exact,
                       baseline_equal=baseline_equal,valid=valid,baseline=a,candidate=b,live=c))
    (OUT/'live_equivalence.json').write_text(json.dumps(checks,indent=2))
    print(job['model'],job['family'],candidate_exact,baseline_equal,valid,flush=True)
assert len(checks)==8 and all(r['candidate_live_exact'] and r['valid'] and
                            (r['model']==3 or r['baseline_equal']) for r in checks)
