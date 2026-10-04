"""Apply the frozen joint pool probe to the other three observed cases."""
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
DEST = HERE/'joint_route_pool_120s'
plan = json.loads((HERE/'plan.json').read_text())
jobs = []
for i, lower in [(0,1246.4190715509233), (2,1595.5469533582743), (3,1591.865289397053)]:
    c = plan['diagnostic_cases'][i]
    jobs.append(dict(id=f"{c['customers']}_{c['pool_label']}", case=c['case'],
        initial_record=str(HERE/'warm_60s_plus_polish_5s'/f'{i:02d}_42_retimed-cg.json'),
        global_lower_bound=lower, time_limit=120, seed=42))
manifest_path = DEST/'manifest.json'
manifest = json.loads(manifest_path.read_text())
assert 'follow_up_jobs' not in manifest
manifest['follow_up_jobs'] = jobs
manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
rows = []
for job in jobs:
    with (DEST/(job['id']+'.log')).open('w') as log:
        run = subprocess.run([sys.executable,str(HERE/'run_joint_pool_probe.py'),json.dumps(job)],
            stdout=log,stderr=subprocess.STDOUT,timeout=145)
    row = dict(id=job['id'],exit=run.returncode)
    if run.returncode == 0:
        data = json.loads((DEST/(job['id']+'.json')).read_text())
        row.update(upper=data['independent_audit']['objective'],gap_percent=data['gap_percent'],
            meets_five_percent=data['meets_five_percent'],runtime=data['result']['info']['runtime'])
    rows.append(row)
    (DEST/'portfolio_runs.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
    print(row,flush=True)
