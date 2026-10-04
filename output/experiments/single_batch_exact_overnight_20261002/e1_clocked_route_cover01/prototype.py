"""Research-only complete E1 clocked route universe, no production hook."""
from collections import Counter
import itertools
import math
import time

from models.routing.common.cycle.precheck import travel_lower_bounds
from models.routing.solvers.exact.strengthening import single_batch_bounds
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
from models.routing.solvers.exact.single_batch_pickup_bound import e1_component_floor


def replay(problem, sequence, windows):
    p=problem.operating_policy;f=problem.fleet;b=p.batches[0]
    if not sequence or any(k=='p' and l=='d' for (_,k),(_,l) in zip(sequence,sequence[1:])):return None
    if len(set(sequence))!=len(sequence):return None
    weight=math.fsum(problem.weights[s,b.id] for s,k in sequence if k=='d')
    volume=sum(problem.demands[s,b.id]*f.container_volume for s,k in sequence if k=='d')
    if weight>f.e1_weight_cap+1e-6 or volume>f.e1_volume_cap+1e-6:return None
    departure=b.start+p.e1_load_time if any(k=='d' for s,k in sequence) else b.cutoff
    clock=departure;previous=problem.depot;previous_kind=None;starts=[];costs=[]
    for site,kind in sequence:
        travel=problem.travel(previous,site)
        arrival=clock+travel
        if kind=='p' and previous_kind in (None,'d'):arrival=max(arrival,b.cutoff+travel)
        low,high=windows[site,kind];start=max(low,arrival)
        if start>high:return None
        starts.append(start);costs.append(problem.distance(previous,site))
        clock=start+problem.service(site)
        weight+=problem.demands[site,b.id]*f.container_weight if kind=='p' else -problem.weights[site,b.id]
        volume+=(1 if kind=='p' else -1)*problem.demands[site,b.id]*f.container_volume
        if not(-1e-6<=weight<=f.e1_weight_cap+1e-6 and -1e-6<=volume<=f.e1_volume_cap+1e-6):return None
        previous,previous_kind=site,kind
    returned=clock+problem.travel(previous,problem.depot)+p.depot_return_unload_time
    latest=min(b.end,p.cycle_end-p.e1_turnaround_time,
        p.cycle_end-p.cleaning_duration if any(k=='p' for s,k in sequence) else p.cycle_end)
    if returned>latest:return None
    costs.append(problem.distance(previous,problem.depot))
    return dict(tasks=tuple(sequence),starts=starts,departure=departure,return_complete=returned,cost=math.fsum(costs))


def enumerate_routes(problem,deadline=None,max_sites=5):
    if problem.model_number!=3 or len(problem.operating_policy.batches)!=1:raise ValueError('B1 M3 only')
    b=problem.operating_policy.batches[0]
    sites=[s for s in problem.satellites if problem.demands[s,b.id]]
    if len(sites)>max_sites:return [],[],dict(complete=False,status='SITE_LIMIT')
    windows=single_batch_bounds(problem,travel_lower_bounds(problem))['visits']
    groups={};raw=[];examined=0;n=len(sites);index={s:i for i,s in enumerate(sites)}
    def check():
        if deadline is not None and time.perf_counter()>=deadline:raise TimeoutError
    try:
        parts=[sequence for size in range(n+1) for sequence in itertools.permutations(sites,size)]
        for ds in parts:
            check()
            for ps in parts:
                check()
                if not ds and not ps:continue
                examined+=1
                sequence=tuple((s,'d') for s in ds)+tuple((s,'p') for s in ps)
                route=replay(problem,sequence,windows)
                if route is None:continue
                mask=sum(1<<(index[s]+(n if k=='p' else 0)) for s,k in sequence)
                route['mask']=mask;raw.append(route)
                old=groups.get(mask)
                if old is None or (route['cost'],route['tasks'])<(old['cost'],old['tasks']):groups[mask]=route
        check()
        return [groups[k] for k in sorted(groups)],raw,dict(complete=True,status='COMPLETE',sites=sites,
            examined=examined,clock_feasible=len(raw),representatives=len(groups))
    except TimeoutError:return [],[],dict(complete=False,status='ENUMERATION_LIMIT',examined=examined)


def bound(problem,time_limit=2,seed=42):
    import gurobipy as gp
    begun=time.perf_counter();deadline=begun+time_limit
    fallback=e1_component_floor(problem)
    output=dict(status='TIME_LIMIT',bound=fallback,fallback=fallback,proven_optimal=False,
        proven_infeasible=False,relaxation_complete=False,problem_sha256=problem_fingerprint(problem),
        proof_scope='B1_M3_complete_clocked_E1_cover_LP_research_only')
    routes,raw,enumeration=enumerate_routes(problem,deadline)
    output['enumeration']=enumeration
    if not enumeration['complete'] or time.perf_counter()>=deadline:
        output.update(status=enumeration['status'],runtime=time.perf_counter()-begun);return output
    m=gp.Model('research_complete_clocked_E1');m.Params.OutputFlag=0;m.Params.Threads=1;m.Params.Seed=seed;m.Params.Method=1
    try:
        tasks=[(s,k) for s in enumeration['sites'] for k in ('d','p')]
        variables=[m.addVar(obj=r['cost']) for r in routes]
        rows=[m.addConstr(gp.quicksum(v for v,r in zip(variables,routes) if task in r['tasks'])==1) for task in tasks]
        fleet=m.addConstr(gp.quicksum(variables)<=problem.fleet.e1_count)
        remaining=deadline-time.perf_counter()
        if remaining>0:
            m.Params.TimeLimit=remaining;m.optimize()
            if m.Status==gp.GRB.OPTIMAL:
                pi={task:row.Pi for task,row in zip(tasks,rows)};nu=min(0.0,fleet.Pi)
                rc=min([0.0]+[r['cost']-math.fsum(pi[k] for k in r['tasks'])-nu for r in raw])
                margin=1e-7*max(1.0,abs(m.ObjVal),math.fsum(abs(v) for v in pi.values())+abs(nu))*(1+len(tasks)+problem.fleet.e1_count)
                certified=math.fsum(pi.values())+problem.fleet.e1_count*(nu+rc)-margin
                output.update(status='LP_OPTIMAL',relaxation_complete=True,objective=m.ObjVal,
                    bound=max(fallback,certified),margin=margin,minimum_reduced_cost=rc,fleet_dual=nu,
                    cover_duals=[[list(k),v] for k,v in pi.items()],routes=routes)
            else:output['status']='MASTER_LIMIT_OR_INFEASIBLE'
    finally:m.dispose()
    output['runtime']=time.perf_counter()-begun
    return output


def project(problem,result):
    from models.routing.common.cycle.audit import audit_solution
    audit=audit_solution(problem,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    windows=single_batch_bounds(problem,travel_lower_bounds(problem))['visits']
    records=[];coverage=Counter()
    for trip in result['e1_trips']:
        sequence=tuple((e['site_id'],e['kind']) for e in trip['events']);r=replay(problem,sequence,windows)
        assert r is not None,(sequence,trip)
        assert r['departure']<=trip['departure'] and r['return_complete']<=trip['return_complete']
        assert all(low<=e['start'] for low,e in zip(r['starts'],trip['events']))
        coverage.update(sequence);records.append(r)
    b=problem.operating_policy.batches[0]
    assert coverage==Counter((s,k) for s in problem.satellites if problem.demands[s,b.id] for k in ('d','p'))
    assert len(records)<=problem.fleet.e1_count and math.isclose(math.fsum(r['cost'] for r in records),audit['distances']['e1'],abs_tol=1e-6)
    return records
