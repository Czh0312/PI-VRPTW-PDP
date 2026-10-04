"""Final 30s optional block-exchange probe using production public options."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
DEST=HERE/'pair_exchange_30s'


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


if len(sys.argv)>1:
    sys.path.insert(0,str(DEST/'source_snapshot'))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.variants.model_5.exact import reoptimize_route_pool
    job=json.loads(sys.argv[1]);initial=json.loads(Path(job['initial_record']).read_text())
    p=load_instance(*load_routing_case(initial['job']['case']))
    before=audit_solution(p,initial['result']);assert before['valid']
    result=reoptimize_route_pool(p,initial['result'],time_limit=30,e1_backend='patterns',
        exchanges=True,max_columns=256,seed=42,verbose=True,diagnostics_dir=DEST/job['id'])
    result=json.loads(json.dumps(result));after=audit_solution(p,result)
    assert after['valid'] and after['periodic_replay']['valid']
    assert after['objective']<=before['objective']+1e-6
    assert result['info']['best_bound'] is None and result['info']['mip_gap'] is None
    write(DEST/(job['id']+'.json'),dict(job=job,result=result,independent_audit=after,
        upper=after['objective'],previous_upper=before['objective'],lower=job['lower'],
        gap_percent=100*(after['objective']-job['lower'])/abs(after['objective'])))
else:
    DEST.mkdir(exist_ok=False)
    for source in [*ROOT.glob('models/**/*.py'),*ROOT.glob('configuration/*.py')]:
        target=DEST/'source_snapshot'/source.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
    prior=json.loads((HERE/'validation.json').read_text())
    jobs=[dict(id=c['id'],initial_record=c['result_path'],lower=c['lower'],time_limit=30,
        e1_backend='patterns',exchanges=True,max_columns=256,seed=42) for c in prior['cases']]
    write(DEST/'manifest.json',dict(jobs=jobs,
        source_sha256={str(f.relative_to(DEST/'source_snapshot')):hashlib.sha256(f.read_bytes()).hexdigest()
            for f in (DEST/'source_snapshot').rglob('*.py')},
        note='Additional exploratory same-kind two-task exchanges. Production opt-in options, defaults unchanged.'))
    rows=[]
    for job in jobs:
        with (DEST/(job['id']+'.log')).open('w') as log:
            r=subprocess.run([sys.executable,str(Path(__file__)),json.dumps(job)],stdout=log,
                stderr=subprocess.STDOUT,timeout=50,env={**os.environ,'PYTHONHASHSEED':'0'})
        row=dict(id=job['id'],exit=r.returncode)
        if r.returncode==0:
            data=json.loads((DEST/(job['id']+'.json')).read_text());row.update(upper=data['upper'],gap_percent=data['gap_percent'])
        rows.append(row);write(DEST/'summary.json',rows);print(row,flush=True)
