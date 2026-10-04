"""Two-way task/order exchanges with the frozen five-site pattern master."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
DEST = HERE/'exchanges_180s'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


if len(sys.argv)>1:
    sys.path.insert(0,str(HERE/'swap_source_snapshot'))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.variants.model_5.exact import reoptimize_route_pool
    job=json.loads(sys.argv[1])
    p=load_instance(*load_routing_case(job['case']))
    previous=json.loads(Path(job['initial_record']).read_text())
    initial=previous['result']
    before=audit_solution(p,initial)
    assert before['valid'] and before['periodic_replay']['valid']
    result=reoptimize_route_pool(p,initial,time_limit=180,seed=42,
        exchanges=True,max_columns=256,verbose=True,diagnostics_dir=DEST/job['id'])
    result=json.loads(json.dumps(result))
    audit=audit_solution(p,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert audit['objective']<=before['objective']+1e-6
    assert result['info']['best_bound'] is None and result['info']['mip_gap'] is None
    assert not result['info']['proven_optimal']
    write(DEST/(job['id']+'.json'),dict(job=job,result=result,initial_audit=before,
        independent_audit=audit,global_lower=previous['best_global_lower'],
        gap_percent=100*(audit['objective']-previous['best_global_lower'])/abs(audit['objective'])))
else:
    DEST.mkdir(exist_ok=False)
    jobs=[dict(id='100_'+label,case=json.loads((HERE/('100_'+label+'.json')).read_text())['job']['case'],
        initial_record=str(HERE/('100_'+label+'.json')),time_limit=180,
        max_columns=256,exchanges=True,seed=42) for label in ('full','tight')]
    write(DEST/'manifest.json',dict(jobs=jobs,
        source_sha256={str(f.relative_to(HERE/'swap_source_snapshot')):hashlib.sha256(f.read_bytes()).hexdigest()
            for f in (HERE/'swap_source_snapshot').rglob('*.py')},
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        scope='Exploratory extra-runtime exchange and larger pool, original audited paths always retained. No global bounds from the restricted master.'))
    rows=[]
    for job in jobs:
        with (DEST/(job['id']+'.log')).open('w') as log:
            r=subprocess.run([sys.executable,str(Path(__file__)),json.dumps(job)],stdout=log,
                stderr=subprocess.STDOUT,timeout=210,env={**os.environ,'PYTHONHASHSEED':'0'})
        row=dict(id=job['id'],exit=r.returncode)
        if r.returncode==0:
            d=json.loads((DEST/(job['id']+'.json')).read_text())
            row.update(upper=d['independent_audit']['objective'],gap_percent=d['gap_percent'],
                runtime=d['result']['info']['runtime'])
        rows.append(row);write(DEST/'summary.json',rows);print(row,flush=True)
