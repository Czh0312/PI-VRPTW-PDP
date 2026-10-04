"""Independent full raw E1 universe/LP and all positive E2 witnesses."""
from pathlib import Path
import hashlib
import itertools
import json
import math
import sys
import time

ROOT=Path(__file__).resolve().parents[4];OUT=Path(__file__).resolve().parent;BATCH=OUT/'paired15_30'
sys.path.insert(0,str(BATCH/'source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
from models.routing.solvers.exact.single_batch_pickup_bound import pickup_contexts,project_original_schedule
from models.routing.solvers.exact.single_batch_e1_bound import enumerate_e1_routes,project_e1_schedule
from models.routing.solvers.exact.multibatch_route_pricing import TripPricingModel
def read(p):return json.loads(p.read_text())
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def independent_e1(p):
    import gurobipy as gp
    b=p.operating_policy.batches[0];f=p.fleet
    sites=[s for s in p.satellites if p.demands[s,b.id]];tasks=[(s,k) for s in sites for k in ('d','p')]
    routes=[]
    for size in range(1,len(tasks)+1):
        for seq in itertools.permutations(tasks,size):
            if any(k=='p' and l=='d' for (_,k),(_,l) in zip(seq,seq[1:])):continue
            w=sum(p.weights[s,b.id] for s,k in seq if k=='d')
            v=sum(p.demands[s,b.id]*f.container_volume for s,k in seq if k=='d')
            feasible=w<=f.e1_weight_cap+1e-6 and v<=f.e1_volume_cap+1e-6
            for s,k in seq:
                w+=p.demands[s,b.id]*f.container_weight if k=='p' else -p.weights[s,b.id]
                v+=(1 if k=='p' else -1)*p.demands[s,b.id]*f.container_volume
                feasible &= -1e-6<=w<=f.e1_weight_cap+1e-6 and -1e-6<=v<=f.e1_volume_cap+1e-6
            if not feasible:continue
            locations=[p.depot]+[s for s,_ in seq]+[p.depot]
            routes.append((seq,sum(p.distance(a,b) for a,b in zip(locations,locations[1:]))))
    model=gp.Model('independent_uncompressed_E1');model.Params.OutputFlag=0;model.Params.Threads=1
    try:
        variables=[model.addVar(obj=cost) for seq,cost in routes]
        for task in tasks:model.addConstr(gp.quicksum(v for v,(seq,_) in zip(variables,routes) if task in seq)==1)
        model.addConstr(gp.quicksum(variables)<=p.fleet.e1_count);model.optimize();assert model.Status==gp.GRB.OPTIMAL
        return tasks,routes,model.ObjVal
    finally:model.dispose()

def main():
    target=BATCH/'verification';target.mkdir();m=read(BATCH/'manifest.json')
    assert sha(BATCH/'compare_frozen.py')==m['driver_sha256']==sha(OUT/'compare.py')
    for path,digest in m['source_sha256'].items():assert sha(BATCH/'source_snapshot'/path)==digest
    for item in m['input_sha256'].values():assert sha(BATCH/item['snapshot'])==item['sha256']
    contexts={};oracles={};projections=[]
    for record in read(BATCH/'projection.json'):
        folder=BATCH/record['case'];cfg,fleet=load_routing_case(folder/'case.json');p=load_instance(cfg,fleet)
        domains,by_site,_=pickup_contexts(p);contexts[record['case']]=(p,by_site)
        start=read(folder/'start.json');a=audit_solution(p,start)
        assert a['valid'] and a['periodic_replay']['valid'] and problem_fingerprint(p)==record['problem_sha256']
        assert sum(len(c['tasks']) for _,c in project_original_schedule(p,start,by_site))==2*len(p.orders)==record['all_tasks']
        routes,e=enumerate_e1_routes(p);assert e['complete'];e1=project_e1_schedule(p,start,routes)
        oracles[record['case']]=independent_e1(p)
        tasks,full,lp=oracles[record['case']]
        assert e['paths']==len(full) and lp<=a['distances']['e1']+1e-6
        projections.append(dict(case=record['case'],tasks=record['all_tasks'],original_objective=a['objective'],
            original_e1=a['distances']['e1'],independent_e1_lp=lp,all_e1_paths=len(full),original_e1_projection=e1,
            audited=True,replay=True,proven_original_integer_optimal=start['info'].get('proven_optimal',False)))
    rows=read(BATCH/'comparison.json');assert len(rows)==m['job_count']==16
    prices=[];pairs={};cover_records=[]
    for row in rows:
        folder=BATCH/row['case']/f'{"candidate" if row["e1_cover"] else "baseline"}_{row["budget"]}_{row["seed"]}'
        assert row==read(folder/'row.json');job=read(folder/'job.json');r=read(folder/'result.json')
        p,by_site=contexts[row['case']];a=audit_solution(p,read(Path(job['start_path'])))
        assert r['problem_sha256']==problem_fingerprint(p) and row['start_sha256']==sha(Path(job['start_path']))
        assert not r['proven_optimal'] and not r['proven_infeasible'] and r['bound']<=a['objective']+1e-6
        assert math.isclose(r['bound'],r['e1_floor']+r['e2_bound'],abs_tol=1e-7)
        if row['e1_cover']:
            c=r['e1_route_cover'];tasks,full,lp=oracles[row['case']]
            assert c['relaxation_complete'] and abs(c['objective']-lp)<1e-6
            pi=dict(zip(tasks,c['cover_duals']));nu=c['fleet_dual'];assert nu<=0
            raw_min=min([0.0]+[cost-math.fsum(pi[k] for k in seq)-nu for seq,cost in full])
            assert c['minimum_reduced_cost']<=raw_min+1e-7
            expected=math.fsum(pi.values())+p.fleet.e1_count*(nu+c['minimum_reduced_cost'])-c['margin']
            assert abs(c['certified_cover_bound']-expected)<1e-6 and c['bound']<=lp+1e-6
            cover_records.append(dict(case=row['case'],seed=row['seed'],budget=row['budget'],raw_route_checks=len(full),
                independently_enumerated_lp=lp,certified=c['bound'],checked=True))
        history=[h for h in r['history'] if h['phase']==2]
        assert math.isclose(r['e2_bound'],max([0.0]+[h['corrected_e2_bound'] for h in history]),abs_tol=1e-7)
        if r['e2_bound']>0:
            best=max(history,key=lambda h:h['corrected_e2_bound'])
            for site,terms in best['prices'].items():
                pi={tuple(k):v for k,v in terms['tasks']};nu=terms['fleet'];reported=best['pricing_lower_bounds'][site]
                deadline=time.perf_counter()+3;pricer=TripPricingModel(p,by_site[site],deadline=deadline,seed=42)
                try:independent=pricer.price(pi,nu,time_limit=max(0,deadline-time.perf_counter()))
                finally:pricer.dispose()
                minimum=independent['reduced_cost'] if independent['optimal'] else independent['lower_bound']
                valid=independent['infeasible'] or reported<=minimum+1e-6
                prices.append(dict(case=row['case'],seed=row['seed'],budget=row['budget'],e1_cover=row['e1_cover'],
                    site=site,reported_lower=reported,independent=independent,checked=valid));write(target/'pricing.json',prices)
        key=(row['case'],row['seed'],row['budget']);pairs.setdefault(key,{})[row['e1_cover']]=row
        print(f'verified {row["case"]} {row["seed"]}/{row["budget"]} e1_cover{row["e1_cover"]}',flush=True)
    paired=[]
    for (case,seed,budget),group in pairs.items():
        assert set(group)=={False,True};b,c=group[False],group[True]
        assert b['start_sha256']==c['start_sha256'];paired.append(dict(case=case,seed=seed,budget=budget,
            baseline=b['bound'],candidate=c['bound'],gain=c['bound']-b['bound'],e1_before=b['e1_floor'],
            e1_after=c['e1_floor'],component_seconds=c['e1_cover_runtime']))
    summary=dict(original_audited_projections=len(projections),root_records=len(rows),pairs=len(paired),
        independent_e1_oracles=len(oracles),e1_witnesses=len(cover_records),
        independently_checked_raw_e1_routes=sum(r['raw_route_checks'] for r in cover_records),
        e2_independent_prices=len(prices),e2_complete_prices=sum(r['independent']['optimal'] for r in prices),
        all_checked=all(r['checked'] for r in prices),root_original_integer_proofs=0,
        preparation_original_integer_proofs=sum(r['proven_original_integer_optimal'] for r in projections),
        limitation='Component and root-strength evidence only, no end-to-end complete-kernel integration/performance')
    write(target/'summary.json',summary);write(target/'pairs.json',paired);write(target/'projections.json',projections)
    write(target/'e1_cover.json',cover_records);print(json.dumps(summary));assert summary['all_checked']

if __name__=='__main__':main()
