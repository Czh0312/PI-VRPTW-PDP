"""Certify unresolved sites once; identical E2 relaxations share valid bounds."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
DEST=HERE/'focused_pricing_150s'
sys.path.insert(0,str(HERE/'source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_route_pricing import route_context
from models.routing.solvers.exact.multibatch_route_cg import site_route_cover


def read(path):
    return json.loads(Path(path).read_text())


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def signature(p,site):
    c=route_context(p,site)
    places=sorted({site}|{o.customer for o in p.orders if o.satellite==site})
    policy=p.operating_policy
    data=dict(site=site,release=c['release'],maximum_trips=c['maximum_trips'],
        nodes=[dict(key=n['key'],order=asdict(n['order']),weight=n['weight'],volume=n['volume'],window=n['window']) for n in c['nodes']],
        caps=[p.fleet.e2_weight_cap,p.fleet.e2_volume_cap],
        policy={k:getattr(policy,k) for k in ('cycle_start','cycle_end','e2_return_unload_time','e2_turnaround_time')},
        last_cutoff=policy.batches[-1].cutoff,
        arcs=[[a,b,p.distance(a,b),p.travel(a,b)] for a in places for b in places])
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()


if len(sys.argv)>1:
    job=read(DEST/'manifest.json')['jobs'][int(sys.argv[1])]
    plans=[read(path) for path in job['seed_plans']]
    p=load_instance(*load_routing_case(plans[0]['job']['case']))
    for plan in plans:
        other=load_instance(*load_routing_case(plan['job']['case']))
        a=audit_solution(other,plan['result'])
        assert a['valid'] and a['periodic_replay']['valid']
        assert signature(other,job['satellite'])==job['relaxation_signature']
    assert signature(p,job['satellite'])==job['relaxation_signature']
    # A bag of individually audited route seeds, not a purported full plan.
    bag=dict(e2_trips=[trip for plan in plans for trip in plan['result']['e2_trips']
        if trip['satellite_id']==job['satellite']])
    started=time.perf_counter()
    site=site_route_cover(p,route_context(p,job['satellite']),
        deadline=started+job['time_limit'],initial_result=bag,seed=42,verbose=True,
        pricing_export_path=DEST/job['satellite']/'best_pricing.mps')
    write(DEST/(job['satellite']+'.json'),dict(job=job,site=site,wall_seconds=time.perf_counter()-started))
else:
    DEST.mkdir(exist_ok=False)
    paths=[str(HERE/'exchanges_180s'/('100_'+pool+'.json')) for pool in ('full','tight')]
    instances=[load_instance(*load_routing_case(read(path)['job']['case'])) for path in paths]
    signatures={site:signature(instances[0],site) for site in instances[0].satellites}
    assert signatures=={site:signature(instances[1],site) for site in instances[1].satellites}
    jobs=[dict(satellite=site,time_limit=150,seed=42,seed_plans=paths,
        relaxation_signature=signatures[site]) for site in ('S1','S4')]
    write(DEST/'manifest.json',dict(jobs=jobs,all_site_signatures=signatures,
        source_sha256={str(f.relative_to(HERE/'source_snapshot')):hashlib.sha256(f.read_bytes()).hexdigest()
            for f in (HERE/'source_snapshot').rglob('*.py')},
        scope='Only E2 relaxation bounds are shared across pools, after exact equality of relevant coefficients, windows, resources and trip limit. E1 bounds remain pool-specific.'))
    rows=[]
    for i,job in enumerate(jobs):
        with (DEST/(job['satellite']+'.log')).open('w') as log:
            r=subprocess.run([sys.executable,str(Path(__file__)),str(i)],stdout=log,
                stderr=subprocess.STDOUT,timeout=175,env={**os.environ,'PYTHONHASHSEED':'0'})
        row=dict(satellite=job['satellite'],exit=r.returncode)
        if r.returncode==0:
            d=read(DEST/(job['satellite']+'.json'));row.update(bound=d['site']['bound'],status=d['site']['status'])
        rows.append(row);write(DEST/'summary.json',rows);print(row,flush=True)
