"""Independent complete-schedule audits and uncompressed E1 proof replay."""
from pathlib import Path
import hashlib
import itertools
import json
import math
import sys

ROOT=Path(__file__).resolve().parents[4];OUT=Path(__file__).resolve().parent;BATCH=OUT/'full15_30'
sys.path.insert(0,str(BATCH/'source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
def read(p):return json.loads(p.read_text())
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def independent_e1(p):
    import gurobipy as gp
    batch=p.operating_policy.batches[0];f=p.fleet;sites=[s for s in p.satellites if p.demands[s,batch.id]]
    tasks=[(s,k) for s in sites for k in ('d','p')];paths=[]
    for size in range(1,len(tasks)+1):
        for sequence in itertools.permutations(tasks,size):
            if any(k=='p' and l=='d' for (_,k),(_,l) in zip(sequence,sequence[1:])):continue
            weight=sum(p.weights[s,batch.id] for s,k in sequence if k=='d')
            volume=sum(p.demands[s,batch.id]*f.container_volume for s,k in sequence if k=='d')
            valid=weight<=f.e1_weight_cap+1e-6 and volume<=f.e1_volume_cap+1e-6
            for s,k in sequence:
                weight+=p.demands[s,batch.id]*f.container_weight if k=='p' else -p.weights[s,batch.id]
                volume+=(1 if k=='p' else -1)*p.demands[s,batch.id]*f.container_volume
                valid &= -1e-6<=weight<=f.e1_weight_cap+1e-6 and -1e-6<=volume<=f.e1_volume_cap+1e-6
            if not valid:continue
            locations=[p.depot]+[s for s,_ in sequence]+[p.depot]
            paths.append((sequence,sum(p.distance(a,b) for a,b in zip(locations,locations[1:]))))
    m=gp.Model('independent_all_E1_paths');m.Params.OutputFlag=0;m.Params.Threads=1
    try:
        variables=[m.addVar(obj=cost) for seq,cost in paths]
        for task in tasks:m.addConstr(gp.quicksum(v for v,(seq,_) in zip(variables,paths) if task in seq)==1)
        m.addConstr(gp.quicksum(variables)<=f.e1_count);m.optimize();assert m.Status==gp.GRB.OPTIMAL
        return tasks,paths,m.ObjVal
    finally:m.dispose()

def main():
    target=BATCH/'verification';target.mkdir();manifest=read(BATCH/'manifest.json')
    assert sha(BATCH/'compare_frozen.py')==manifest['driver_sha256']==sha(OUT/'compare.py')
    for p,h in manifest['source_sha256'].items():assert sha(BATCH/'source_snapshot'/p)==h
    for item in manifest['input_sha256'].values():assert sha(BATCH/item['snapshot'])==item['sha256']
    rows=read(BATCH/'comparison.json');assert len(rows)==manifest['job_count']==24
    contexts={};oracles={};starts={}
    for record in read(BATCH/'projection.json'):
        folder=BATCH/record['case'];cfg,fleet=load_routing_case(folder/'case.json');p=load_instance(cfg,fleet)
        start=read(folder/'start.json');a=audit_solution(p,start)
        assert a['valid'] and a['periodic_replay']['valid'] and problem_fingerprint(p)==record['problem_sha256']
        assert record['actual_row_value']>=record['e1_certificate']['bound']-1e-6
        contexts[record['case']]=p;oracles[record['case']]=independent_e1(p);starts[record['case']]=sha(folder/'start.json')
    audits=[];certificates=[];groups={};failed=[]
    for row in rows:
        folder=BATCH/row['case']/f'{"candidate" if row["candidate"] else "baseline"}_{row["budget"]}_{row["seed"]}'
        if row['status'] in ('ERROR','WORKER_TIMEOUT'):
            failed.append(row);continue
        assert row==read(folder/'row.json');r=read(folder/'result.json');p=contexts[row['case']];a=audit_solution(p,r)
        assert a['valid'] and a['periodic_replay']['valid']
        assert row['problem_sha256']==problem_fingerprint(p) and row['start_sha256']==starts[row['case']]
        assert row['full_tasks']==2*len(p.orders) and row['LB']<=a['objective']+1e-6
        assert abs(row['Gap']-max(0,row['UB']-row['LB'])/max(1e-9,abs(row['UB'])))<1e-6
        if row['proven_optimal']:assert abs(row['UB']-row['LB'])<1e-6
        if row['candidate']:
            c=r['info']['e1_cover_certificate'];tasks,paths,lp=oracles[row['case']]
            assert c['problem_sha256']==problem_fingerprint(p) and c['proof_scope']=='B1_M3_E1_complete_clock_relaxed_cover_LP'
            assert c['bound']<=lp+1e-6 and c['bound']<=a['distances']['e1']+1e-6
            if c['relaxation_complete']:
                pi=dict(zip(tasks,c['cover_duals']));nu=c['fleet_dual'];assert nu<=0
                raw_min=min([0.0]+[cost-math.fsum(pi[k] for k in seq)-nu for seq,cost in paths])
                assert c['minimum_reduced_cost']<=raw_min+1e-6
                expected=math.fsum(pi.values())+p.fleet.e1_count*(nu+c['minimum_reduced_cost'])-c['margin']
                assert abs(expected-c['certified_cover_bound'])<1e-6 and abs(c['objective']-lp)<1e-6
            assert row['component_applied']
            certificates.append(dict(case=row['case'],seed=row['seed'],budget=row['budget'],bound=c['bound'],
                independently_enumerated_lp=lp,raw_routes_checked=len(paths),checked=True))
        else:assert not row['component_applied'] and 'e1_cover_certificate' not in r['info']
        audits.append(dict(case=row['case'],seed=row['seed'],budget=row['budget'],candidate=row['candidate'],
            objective=a['objective'],LB=row['LB'],Gap=row['Gap'],audited=True,replay=True,original_integer_proof=row['proven_optimal']))
        groups.setdefault((row['case'],row['seed'],row['budget']),{})[row['candidate']]=row
    pairs=[]
    for (case,seed,budget),g in groups.items():
        if set(g)!={False,True}:continue
        b,c=g[False],g[True];assert b['start_sha256']==c['start_sha256']
        pairs.append(dict(case=case,seed=seed,budget=budget,baseline=b,candidate=c,
            UB_change=c['UB']-b['UB'],LB_change=c['LB']-b['LB'],Gap_change=c['Gap']-b['Gap']))
    summary=dict(successful_full_schedules=len(audits),audits=len(audits),periodic_replays=len(audits),
        independent_E1_oracles=len(oracles),candidate_certificates=len(certificates),raw_E1_route_checks=sum(r['raw_routes_checked'] for r in certificates),
        pairs=len(pairs),failed_runs=len(failed),complete_integer_proof_records=sum(r['original_integer_proof'] for r in audits),
        distinct_complete_integer_proved_cases=len({r['case'] for r in audits if r['original_integer_proof']}),
        limitation='Full original integer proofs distinguished from complete E1 LP; source2 already had optimal initial schedule. Ambient load recorded, not CPU-exclusive.')
    write(target/'summary.json',summary);write(target/'pairs.json',pairs);write(target/'audits.json',audits)
    write(target/'certificates.json',certificates);write(target/'failures.json',failed);print(json.dumps(summary))
    assert not failed,'Retain all failures; adoption requires complete evidence'

if __name__=='__main__':main()
