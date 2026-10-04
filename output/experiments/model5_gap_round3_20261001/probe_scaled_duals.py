"""Affine repair of arbitrary pricing witnesses with safe route cost floors."""
from pathlib import Path
import json,math,sys
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
OLD=HERE.parent/'model5_gap_round2_20261001'
sys.path.insert(0,str(HERE/'source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance

def read(p):return json.loads(Path(p).read_text())
def floors(problem,site):
    orders=[o for o in problem.orders if o.satellite==site]
    places=sorted({site}|{o.customer for o in orders})
    dist={(a,b):problem.distance(a,b) for a in places for b in places}
    for m in places:
        for a in places:
            for b in places:dist[a,b]=min(dist[a,b],dist[a,m]+dist[m,b])
    result={}
    for category in [None]+sorted({o.batch for o in orders}):
        raw=min((dist[site,o.customer]+dist[o.customer,site] for o in orders
            if category is None or o.batch==category),default=0)
        result[category]=max(0,raw-1e-7*max(1,raw))
    return result

def repair(w, costs):
    pi={tuple(k):v for k,v in w['duals']};nu=w['fleet_dual'];K=w['maximum_trips']
    groups=[g for g in w['pricing']['groups'] if not g['infeasible']]
    lines=[(costs[g['category']],g['lower_bound']-costs[g['category']]) for g in groups]
    alphas={0.0,1.0}
    for a,b in lines:
        if b:alphas.add(-a/b)
        for c,d in lines:
            if b!=d:alphas.add((c-a)/(b-d))
    rows=[]
    for alpha in alphas:
        if not 0<=alpha<=1:continue
        rho=min((a+alpha*b for a,b in lines),default=0)
        value=alpha*(math.fsum(pi.values())+K*nu)+K*min(0,rho)
        rows.append((value,alpha,rho))
    value,alpha,rho=max(rows)
    return dict(bound=value,alpha=alpha,pricing_lower=rho,original=w['candidate_bound'],cost_floors=[[k,v] for k,v in costs.items()])

if __name__=='__main__':
    p=load_instance(*load_routing_case(read(OLD/'100_full.json')['job']['case']))
    candidates=[]
    files=[OLD/'focused_pricing_150s'/f'{s}.json' for s in ('S1','S4')]
    files += sorted((HERE/'batch_cg_180s').glob('S*.json'))
    for path in files:
        payload=read(path);site=payload['site'];costs=floors(p,site['satellite'])
        rows=[dict(index=i,**repair(w,costs)) for i,w in enumerate(site['iterations']) if w['method']=='integer_pricing' and w['phase']=='distance']
        best=max(rows,key=lambda r:r['bound'])
        candidates.append(dict(source=str(path),site=site['satellite'],raw_site_bound=site['bound'],best=best))
    (HERE/'scaled_dual_probe.json').write_text(json.dumps(candidates,indent=2,allow_nan=False)+'\n')
    print(json.dumps(candidates,indent=2),flush=True)
