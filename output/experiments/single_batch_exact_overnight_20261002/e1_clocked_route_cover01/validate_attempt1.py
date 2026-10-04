"""Independent fixed-path integer clock feasibility and original projections."""
from pathlib import Path
from copy import copy,deepcopy
import hashlib
import itertools
import json
import math
import sys

OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(OUT/'source_snapshot'))
from prototype import enumerate_routes,bound,project
from tests.routing.test_single_batch_solver_audit import tiny_problem,enumerate_tiny
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance


def independent_path(p,seq):
    import gurobipy as gp
    b=p.operating_policy.batches[0];policy=p.operating_policy;f=p.fleet
    if any(k=='p' and l=='d' for (_,k),(_,l) in zip(seq,seq[1:])):return None
    w=sum(p.weights[s,b.id] for s,k in seq if k=='d');v=sum(p.demands[s,b.id]*f.container_volume for s,k in seq if k=='d')
    if w>f.e1_weight_cap+1e-6 or v>f.e1_volume_cap+1e-6:return None
    for s,k in seq:
        w+=p.demands[s,b.id]*f.container_weight if k=='p' else -p.weights[s,b.id]
        v+=(1 if k=='p' else -1)*p.demands[s,b.id]*f.container_volume
        if not(-1e-6<=w<=f.e1_weight_cap+1e-6 and -1e-6<=v<=f.e1_volume_cap+1e-6):return None
    locations=list(p.network.nodes)
    fw={a:{z:p.travel(a,z) for z in locations} for a in locations}
    for k in locations:
        for a in locations:
            for z in locations:fw[a][z]=min(fw[a][z],fw[a][k]+fw[k][z])
    windows={}
    for s,k in seq:
        if k=='d':
            earliest=b.start+policy.e1_load_time+fw[p.depot][s]
            latest=min(min(o.delivery_window[1],b.cutoff-o.service_time)-policy.e2_dispatch_prep_time-fw[s][o.customer]
                for o in p.orders if o.satellite==s)-p.service(s)
        else:
            earliest=b.cutoff
            latest=min(b.end,policy.cycle_end-policy.cleaning_duration,policy.cycle_end-policy.e1_turnaround_time)
            latest-=p.service(s)+fw[s][p.depot]+policy.depot_return_unload_time
        if earliest>latest:return None
        windows[s,k]=(earliest,latest)
    m=gp.Model('independent_fixed_E1_path_integer_clocks');m.Params.OutputFlag=0;m.Params.Threads=1
    try:
        d=m.addVar(vtype=gp.GRB.INTEGER,lb=policy.cycle_start,ub=policy.cycle_end)
        if any(k=='d' for s,k in seq):m.addConstr(d==b.start+policy.e1_load_time)
        else:m.addConstr(d>=b.cutoff)
        times=[m.addVar(vtype=gp.GRB.INTEGER,lb=windows[task][0],ub=windows[task][1]) for task in seq]
        previous=p.depot;previous_time=d;previous_service=0;previous_kind=None
        for (s,k),t in zip(seq,times):
            m.addConstr(t>=previous_time+previous_service+p.travel(previous,s))
            if k=='p' and previous_kind in (None,'d'):m.addConstr(t>=b.cutoff+p.travel(previous,s))
            previous,previous_time,previous_service,previous_kind=s,t,p.service(s),k
        e=m.addVar(vtype=gp.GRB.INTEGER,lb=policy.cycle_start,ub=policy.cycle_end)
        m.addConstr(e>=previous_time+previous_service+p.travel(previous,p.depot)+policy.depot_return_unload_time)
        m.addConstr(e<=b.end);m.addConstr(e+policy.e1_turnaround_time<=policy.cycle_end)
        if any(k=='p' for s,k in seq):m.addConstr(e+policy.cleaning_duration<=policy.cycle_end)
        m.optimize()
        if m.Status!=gp.GRB.OPTIMAL:return None
        path=[p.depot]+[s for s,k in seq]+[p.depot]
        return sum(p.distance(a,z) for a,z in zip(path,path[1:]))
    finally:m.dispose()


def main():
    destination=OUT/'verification';destination.mkdir()
    manifest=json.loads((OUT/'prepared_manifest.json').read_text())
    for name,expected_hash in manifest['files_sha256'].items():
        assert hashlib.sha256((OUT/name).read_bytes()).hexdigest()==expected_hash,name
    small=[];pathchecks=0
    cases=[(f'seed{seed}',tiny_problem(3,seed)) for seed in range(12)]
    for weight,volume in [(4,20),(20,2),(4,2),(3,20)]:
        p=copy(tiny_problem(3,1));p.fleet=copy(p.fleet);p.fleet.e1_count=2;p.fleet.e1_weight_cap=weight;p.fleet.e1_volume_cap=volume
        cases.append((f'capacity{weight}_{volume}',p))
    # Cheapest clock-free representative fails; a dearer same-mask path is
    # feasible. Mutate this independent synthetic case only, never inputs.
    p=deepcopy(tiny_problem(3,1));p._travel['T','S']=100;p._travel['S','T']=1
    index=p.network.node_index
    p.network.dist[index['T']][index['S']]=1;p.network.dist[index['S']][index['T']]=20
    cases.append(('nonmetric_clock_cost_tradeoff',p))
    for name,p in cases:
        b=p.operating_policy.batches[0];tasks=[(s,k) for s in p.satellites if p.demands[s,b.id] for k in ('d','p')]
        expected={};raw={}
        for size in range(1,len(tasks)+1):
            for seq in itertools.permutations(tasks,size):
                cost=independent_path(p,seq);pathchecks+=1
                if cost is not None:raw[seq]=cost;expected[frozenset(seq)]=min(expected.get(frozenset(seq),math.inf),cost)
        routes,paths,metadata=enumerate_routes(p)
        assert metadata['complete'] and {r['tasks']:r['cost'] for r in paths}==raw
        assert {frozenset(r['tasks']):r['cost'] for r in routes}==expected
        cert=bound(p,2)
        if name.startswith('seed'):
            original=enumerate_tiny(p)
            if math.isfinite(original):assert cert['bound']<=original+1e-6
        if name=='nonmetric_clock_cost_tradeoff':
            key=frozenset((s,'d') for s in p.satellites)
            assert key in expected and (('T','d'),('S','d')) not in raw and (('S','d'),('T','d')) in raw
        assert not cert['proven_optimal'] and not cert['proven_infeasible']
        small.append(dict(case=name,all_task_sequences_checked=sum(math.perm(len(tasks),i) for i in range(1,len(tasks)+1)),
            feasible_paths=len(raw),representatives=len(expected),bound=cert['bound'],status=cert['status']))
    partial=bound(tiny_problem(3,1),1e-9)
    assert not partial['relaxation_complete'] and partial['bound']==partial['fallback']
    assert not enumerate_routes(tiny_problem(3,1),max_sites=0)[2]['complete']
    base=OUT/'inputs_snapshot';large=[]
    for item in json.loads((base/'projection.json').read_text()):
        folder=base/item['case'];cfg,fleet=load_routing_case(folder/'case.json');p=load_instance(cfg,fleet)
        initial=json.loads((folder/'start.json').read_text());projection=project(p,initial)
        frozen_clockfree=item['e1_certificate'];clocked=bound(p,2)
        assert clocked['bound']<=sum(r['cost'] for r in projection)+1e-6
        b=p.operating_policy.batches[0]
        tasks=[(s,k) for s in p.satellites if p.demands[s,b.id] for k in ('d','p')]
        independent_raw={}
        for size in range(1,len(tasks)+1):
            for seq in itertools.permutations(tasks,size):
                cost=independent_path(p,seq);pathchecks+=1
                if cost is not None:independent_raw[seq]=cost
        _,all_paths,complete=enumerate_routes(p)
        assert complete['complete'] and {r['tasks']:r['cost'] for r in all_paths}==independent_raw
        if clocked['relaxation_complete']:
            pi={tuple(k):v for k,v in clocked['cover_duals']};nu=clocked['fleet_dual']
            minimum=min([0.0]+[cost-sum(pi[k] for k in seq)-nu for seq,cost in independent_raw.items()])
            assert nu<=0 and clocked['minimum_reduced_cost']<=minimum+1e-6
            certified=sum(pi.values())+p.fleet.e1_count*(nu+clocked['minimum_reduced_cost'])-clocked['margin']
            assert abs(clocked['bound']-max(clocked['fallback'],certified))<1e-6
        large.append(dict(case=item['case'],problem_sha256=clocked['problem_sha256'],
            original_start_sha256=hashlib.sha256((folder/'start.json').read_bytes()).hexdigest(),
            original_task_count=2*len(p.orders),projected_original_e1=projection,
            old_component=frozen_clockfree,new_component=clocked,
            improvement=clocked['bound']-frozen_clockfree['bound'],
            independently_clock_feasible_paths=len(independent_raw),independent_dual_checked=True))
    summary=dict(small_cases=len(small),integer_path_checks=pathchecks,large_unchanged_projections=len(large),
        scope='Research-only component-strength diagnostic, no end-to-end speed claim or default adoption',
        stronger_large_cases=sum(r['improvement']>1e-6 for r in large),partial_universe_fallback_checked=True)
    for name,value in [('tiny_paths',small),('large_components',large),('summary',summary)]:
        (destination/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary))

if __name__=='__main__':main()
