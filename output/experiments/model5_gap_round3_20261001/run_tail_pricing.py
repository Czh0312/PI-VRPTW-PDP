"""Registered cold-start full-domain pricing comparison at frozen dual prices."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,time
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OLD=HERE.parent/'model5_gap_round2_20261001'
sys.path.insert(0,str(HERE/'source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_route_pricing import RoutePricer,route_context
from models.routing.solvers.exact.multibatch_route_cg import dual_certificate

def read(path): return json.loads(Path(path).read_text())
def write(path,value): path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n')

if len(sys.argv)>1:
    job=read(HERE/'fixed_duals/manifest.json')['jobs'][int(sys.argv[1])]
    saved=read(HERE/'batch_cg_180s'/(job['site']+'.json'))
    witness=next(w for w in reversed(saved['site']['iterations']) if w['method']=='integer_pricing' and w['phase']=='distance')
    duals={tuple(k):v for k,v in witness['duals']};nu=witness['fleet_dual']
    base=read(OLD/'pair_exchange_30s/100_full.json')
    p=load_instance(*load_routing_case(read(OLD/'100_full.json')['job']['case']));c=route_context(p,job['site'])
    start=time.perf_counter();pricer=RoutePricer(p,c,connectivity=job['connectivity'],verbose=False)
    try:
        result=pricer.price(duals,nu,time_limit=job['time_limit'],stop_at_negative=False)
        path=HERE/'tail_pricing_40s'/job['id'];path.mkdir()
        pricer.write(path/'pricing.mps')
        value=dual_certificate(duals,nu,c['maximum_trips'],result['lower_bound'])
        write(HERE/'tail_pricing_40s'/(job['id']+'.json'),dict(job=job,source_witness=witness,
            pricing=result,candidate_bound=value,wall_seconds=time.perf_counter()-start))
        print(dict(id=job['id'],pricing_lower=result['lower_bound'],candidate_bound=value,
            reduced_cost=result['reduced_cost'],status=result['status']),flush=True)
    finally: pricer.dispose()
else:
    dest=HERE/'tail_pricing_40s';dest.mkdir(exist_ok=False)
    jobs=[dict(id=site+'_'+variant,site=site,connectivity=flag,time_limit=40,seed=42)
        for site in ('S1','S4') for variant,flag in [('base',False),('flow',True)]]
    write(dest/'manifest.json',dict(jobs=jobs,source_sha256={str(f.relative_to(HERE/'source_snapshot')):hashlib.sha256(f.read_bytes()).hexdigest() for f in (HERE/'source_snapshot').rglob('*.py')}))
    summary=[]
    for i,job in enumerate(jobs):
        with (dest/(job['id']+'.log')).open('w') as log:
            run=subprocess.run([sys.executable,str(Path(__file__)),str(i)],stdout=log,stderr=subprocess.STDOUT,
                timeout=65,env={**os.environ,'PYTHONHASHSEED':'0'})
        row=dict(id=job['id'],exit=run.returncode)
        if run.returncode==0:
            data=read(dest/(job['id']+'.json'));row.update(candidate_bound=data['candidate_bound'],
                lower=data['pricing']['lower_bound'],reduced=data['pricing']['reduced_cost'],wall=data['wall_seconds'])
        summary.append(row);write(dest/'summary.json',summary);print(row,flush=True)
