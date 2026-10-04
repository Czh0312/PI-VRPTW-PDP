"""Bounded pair task-set enumeration is an audited primal neighborhood only."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,time
HERE=Path(__file__).resolve().parent;OLD=HERE.parent/'model5_gap_round2_20261001'
sys.path.insert(0,str(HERE/'recombine_source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_route_pool import reoptimize_route_pool
from models.routing.common.cycle.audit import audit_solution
DEST=HERE/'recombine_120s'
def read(p):return json.loads(Path(p).read_text())
def write(p,v):p.write_text(json.dumps(v,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
if len(sys.argv)>1:
    job=read(DEST/'manifest.json')['jobs'][int(sys.argv[1])]
    p=load_instance(*load_routing_case(job['case']));initial=read(job['initial'])['result']
    assert audit_solution(p,initial)['valid']
    start=time.perf_counter();result=reoptimize_route_pool(p,initial,time_limit=120,seed=42,
        exchanges=True,recombinations=True,max_columns=512,max_passes=4,e1_backend='patterns',
        diagnostics_dir=DEST/job['id'],verbose=False)
    write(DEST/(job['id']+'.json'),dict(job=job,result=result,wall_seconds=time.perf_counter()-start))
else:
    DEST.mkdir(exist_ok=False)
    jobs=[dict(id='100_'+pool,case=read(OLD/('100_'+pool+'.json'))['job']['case'],
        initial=str(OLD/initial),time_limit=120,seed=42,exchanges=True,recombinations=True,max_columns=512,
        e1_backend='patterns',max_passes=4) for pool,initial in [('full','pair_exchange_30s/100_full.json'),('tight','exchanges_180s/100_tight.json')]]
    write(DEST/'manifest.json',dict(jobs=jobs,registered_before_run=True,
        source_sha256={str(f.relative_to(HERE/'recombine_source_snapshot')):hashlib.sha256(f.read_bytes()).hexdigest() for f in (HERE/'recombine_source_snapshot').rglob('*.py')}))
    summary=[]
    for i,j in enumerate(jobs):
        with (DEST/(j['id']+'.log')).open('w') as log:
            r=subprocess.run([sys.executable,str(Path(__file__)),str(i)],stdout=log,stderr=subprocess.STDOUT,
                timeout=150,env={**os.environ,'PYTHONHASHSEED':'0'})
        row=dict(id=j['id'],exit=r.returncode)
        if not r.returncode:
            d=read(DEST/(j['id']+'.json'));row.update(upper=d['result']['info']['objective'],
                improved=d['result']['info']['route_pool_improved'],wall=d['wall_seconds'])
        summary.append(row);write(DEST/'summary.json',summary);print(row,flush=True)
