"""Full-domain multi-category pricing probe; bounds remain dual certificates."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,time
HERE=Path(__file__).resolve().parent;OLD=HERE.parent/'model5_gap_round2_20261001'
sys.path.insert(0,str(HERE/'source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_route_pricing import route_context
from models.routing.solvers.exact.multibatch_route_cg import site_route_cover
from models.routing.common.cycle.audit import audit_solution

def read(p):return json.loads(Path(p).read_text())
def write(p,v):p.write_text(json.dumps(v,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
DEST=HERE/'batch_cg_180s'
if len(sys.argv)>1:
    job=read(DEST/'manifest.json')['jobs'][int(sys.argv[1])]
    p=load_instance(*load_routing_case(read(OLD/'100_full.json')['job']['case']))
    seeds=[read(OLD/'pair_exchange_30s/100_full.json')['result'],read(OLD/'exchanges_180s/100_tight.json')['result']]
    assert audit_solution(p,seeds[0])['valid']
    bag=dict(e2_trips=[t for r in seeds for t in r['e2_trips'] if t['satellite_id']==job['site']])
    start=time.perf_counter()
    result=site_route_cover(p,route_context(p,job['site']),deadline=start+job['time_limit'],
        initial_result=bag,batch_pricing=True,connectivity=False,pricing_time_limit=4,seed=42,
        pricing_export_path=DEST/job['site']/'best_pricing.mps',verbose=False)
    write(DEST/(job['site']+'.json'),dict(job=job,site=result,wall_seconds=time.perf_counter()-start))
else:
    DEST.mkdir(exist_ok=False)
    jobs=[dict(site=s,time_limit=180,batch_pricing=True,connectivity=False,pricing_time_limit=4,
        seed=42,case=read(OLD/'100_full.json')['job']['case']) for s in ('S1','S4')]
    write(DEST/'manifest.json',dict(jobs=jobs,registered_before_run=True,
        source_sha256={str(f.relative_to(HERE/'source_snapshot')):hashlib.sha256(f.read_bytes()).hexdigest() for f in (HERE/'source_snapshot').rglob('*.py')}))
    summary=[]
    for i,j in enumerate(jobs):
        with (DEST/(j['site']+'.log')).open('w') as log:
            r=subprocess.run([sys.executable,str(Path(__file__)),str(i)],stdout=log,stderr=subprocess.STDOUT,
                timeout=210,env={**os.environ,'PYTHONHASHSEED':'0'})
        row=dict(site=j['site'],exit=r.returncode)
        if not r.returncode:
            d=read(DEST/(j['site']+'.json'));row.update(bound=d['site']['bound'],status=d['site']['status'],
                iterations=len(d['site']['iterations']),columns=len(d['site']['columns']),wall=d['wall_seconds'])
        summary.append(row);write(DEST/'summary.json',summary);print(row,flush=True)
