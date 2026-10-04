"""Resume with individually replayable prior columns, retaining the full domain."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,time
HERE=Path(__file__).resolve().parent;OLD=HERE.parent/'model5_gap_round2_20261001'
sys.path.insert(0,str(HERE/'final_source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_route_pricing import route_context,evaluate_column
from models.routing.solvers.exact.multibatch_route_cg import site_route_cover
DEST=HERE/'resume_cg'
def read(p):return json.loads(Path(p).read_text())
def write(p,v):p.write_text(json.dumps(v,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
if len(sys.argv)>1:
    job=read(DEST/'manifest.json')['jobs'][int(sys.argv[1])]
    p=load_instance(*load_routing_case(job['case']));context=route_context(p,job['site'])
    columns=read(HERE/'batch_cg_180s'/(job['site']+'.json'))['site']['columns']
    columns+=read(HERE/'tail_pricing_40s'/(job['site']+'_base.json'))['pricing']['columns']
    for c in columns:
        assert evaluate_column(p,context,c['tasks']) is not None
    bag=dict(e2_trips=[dict(satellite_id=job['site'],events=[dict(order_id=oid,kind=kind) for oid,kind in c['tasks']]) for c in columns])
    start=time.perf_counter()
    site=site_route_cover(p,context,deadline=start+job['time_limit'],initial_result=bag,
        batch_pricing=True,dual_repair=True,pricing_time_limit=8,seed=42,verbose=False,
        pricing_export_path=DEST/job['site']/'best_pricing.mps')
    write(DEST/(job['site']+'.json'),dict(job=job,site=site,wall_seconds=time.perf_counter()-start))
else:
    DEST.mkdir(exist_ok=False)
    jobs=[dict(site=site,time_limit=seconds,seed=42,batch_pricing=True,dual_repair=True,pricing_time_limit=8,
        case=read(OLD/'100_full.json')['job']['case']) for site,seconds in [('S4',150),('S1',120)]]
    write(DEST/'manifest.json',dict(jobs=jobs,registered_before_run=True,
        source_sha256={str(f.relative_to(HERE/'final_source_snapshot')):hashlib.sha256(f.read_bytes()).hexdigest() for f in (HERE/'final_source_snapshot').rglob('*.py')}))
    summary=[]
    for i,j in enumerate(jobs):
        with (DEST/(j['site']+'.log')).open('w') as log:
            r=subprocess.run([sys.executable,str(Path(__file__)),str(i)],stdout=log,stderr=subprocess.STDOUT,
                timeout=j['time_limit']+30,env={**os.environ,'PYTHONHASHSEED':'0'})
        row=dict(site=j['site'],exit=r.returncode)
        if not r.returncode:
            d=read(DEST/(j['site']+'.json'));row.update(bound=d['site']['bound'],status=d['site']['status'],iterations=len(d['site']['iterations']),wall=d['wall_seconds'])
        summary.append(row);write(DEST/'summary.json',summary);print(row,flush=True)
