"""Complete B1 M3 E1 route-cover relaxation; never an integer proof.

Original E1 geometry explicitly forbids pickup-to-delivery edges. Relaxing
clocks, E2 synchronization and stocks leaves two independent maximum loads:
the sum of deliveries at departure and the sum of pickups at return. Thus
routes with the same tasks have identical coefficients and feasibility, and
only their cheapest RAW directed path is needed. Interrupted enumeration
does not certify any restricted-pool objective.
"""
from collections import Counter
import itertools
import math
import time


def _sites(problem):
    if problem.model_number!=3 or len(problem.operating_policy.batches)!=1:
        raise ValueError('E1 route cover requires B1 M3')
    batch=problem.operating_policy.batches[0]
    return [s for s in problem.satellites if problem.demands[s,batch.id]]


def enumerate_e1_routes(problem,*,deadline=None,max_sites=5):
    """Complete minimum-cost representative for every feasible task subset."""
    sites=_sites(problem);n=len(sites);f=problem.fleet;b=problem.operating_policy.batches[0]
    if n>max_sites:return [],dict(complete=False,status='SITE_LIMIT',sites=sites,paths=0)
    groups={};examined=0
    def check():
        if deadline is not None and time.perf_counter()>=deadline:raise TimeoutError
    try:
        # Precompute both kinds' raw depot/path costs; no shortest-path metric
        # substitution, and a warehouse delivery/pickup remains distinct.
        paths={kind:[] for kind in ('d','p')}
        for kind in paths:
            for size in range(n+1):
                for sequence in itertools.permutations(range(n),size):
                    check();quantity=sum(problem.demands[sites[i],b.id] for i in sequence)
                    weight=(sum(problem.weights[sites[i],b.id] for i in sequence) if kind=='d'
                        else quantity*f.container_weight)
                    if weight>f.e1_weight_cap+1e-6 or quantity*f.container_volume>f.e1_volume_cap+1e-6:continue
                    mask=sum(1<<(i+(n if kind=='p' else 0)) for i in sequence)
                    internal=math.fsum(problem.distance(sites[i],sites[j]) for i,j in zip(sequence,sequence[1:]))
                    paths[kind].append((sequence,mask,internal))
        for ds,dm,dc in paths['d']:
            for ps,pm,pc in paths['p']:
                if examined%128==0:check()
                if not ds and not ps:continue
                examined+=1;sequence=ds+ps
                cost=math.fsum([problem.distance(problem.depot,sites[sequence[0]]),dc,pc,
                    problem.distance(sites[sequence[-1]],problem.depot)]+
                    ([problem.distance(sites[ds[-1]],sites[ps[0]])] if ds and ps else []))
                tasks=tuple((sites[i],'d') for i in ds)+tuple((sites[i],'p') for i in ps)
                mask=dm|pm;old=groups.get(mask)
                if old is None or (cost,tasks)<(old['cost'],old['tasks']):
                    groups[mask]=dict(mask=mask,tasks=tasks,cost=cost)
        check()
        return [groups[k] for k in sorted(groups)],dict(complete=True,status='COMPLETE',sites=sites,paths=examined)
    except TimeoutError:
        return [],dict(complete=False,status='ENUMERATION_LIMIT',sites=sites,paths=examined)


def project_e1_schedule(problem,result,routes):
    """Original trip projects unchanged before same-cover cost dominance."""
    from models.routing.common.cycle.audit import audit_solution
    sites=_sites(problem);n=len(sites);index={s:i for i,s in enumerate(sites)}
    representatives={r['mask']:r for r in routes};audit=audit_solution(problem,result)
    if not audit['valid'] or not audit['periodic_replay']['valid']:
        raise ValueError('E1 projection requires audited original integer schedule')
    records=[];coverage=Counter();batch=problem.operating_policy.batches[0]
    for trip in result['e1_trips']:
        tasks=tuple((e['site_id'],e['kind']) for e in trip['events'])
        if any(k=='p' and next_kind=='d' for (_,k),(_,next_kind) in zip(tasks,tasks[1:])):
            raise RuntimeError('Original E1 violated delivery-first geometry')
        if len(set(tasks))!=len(tasks):raise RuntimeError('Repeated original warehouse task')
        for e in trip['events']:
            if e['quantity']!=problem.demands[e['site_id'],batch.id]:raise RuntimeError('Original E1 quantity changed')
        mask=sum(1<<(index[s]+(n if k=='p' else 0)) for s,k in tasks)
        raw=math.fsum(problem.distance(a,b) for a,b in zip(
            [problem.depot]+[s for s,_ in tasks],[s for s,_ in tasks]+[problem.depot]))
        representative=representatives[mask]
        if representative['cost']>raw+1e-7:raise RuntimeError('Complete universe failed original route dominance')
        records.append(dict(tasks=tasks,raw_cost=raw,representative_cost=representative['cost'],mask=mask))
        coverage.update(tasks)
    if coverage!=Counter((s,k) for s in sites for k in ('d','p')):
        raise RuntimeError('E1 projection lost warehouse tasks')
    if len(records)>problem.fleet.e1_count:raise RuntimeError('E1 physical vehicle bound invalid')
    if not math.isclose(sum(r['raw_cost'] for r in records),audit['distances']['e1'],abs_tol=1e-7):
        raise RuntimeError('E1 projection changed raw original distance')
    return records


def e1_route_cover_bound(problem,time_limit=2,*,seed=42,max_sites=5):
    """Certified complete-universe LP floor; interrupted cases use old floor."""
    if not math.isfinite(time_limit) or time_limit<=0:raise ValueError('E1 budget must be finite and positive')
    sites=_sites(problem)
    from .single_batch_pickup_bound import e1_component_floor
    begun=time.perf_counter();deadline=begun+time_limit;fallback=e1_component_floor(problem)
    output=dict(proof_scope='B1_M3_E1_complete_clock_relaxed_cover_LP',bound=fallback,fallback=fallback,
        proven_optimal=False,proven_infeasible=False,relaxation_complete=False,sites=sites,
        original_physical_model_unchanged=True)
    routes,enumeration=enumerate_e1_routes(problem,deadline=deadline,max_sites=max_sites)
    output.update(enumeration=enumeration,route_count=len(routes))
    if not enumeration['complete'] or time.perf_counter()>=deadline:
        output.update(status=enumeration['status'],runtime=time.perf_counter()-begun);return output
    import gurobipy as gp
    model=gp.Model('B1_M3_complete_E1_cover');model.Params.OutputFlag=0;model.Params.Threads=1
    model.Params.Method=1;model.Params.Seed=seed
    try:
        variables=[model.addVar(obj=r['cost']) for r in routes]
        cover=[model.addConstr(gp.quicksum(v for v,r in zip(variables,routes) if (s,k) in r['tasks'])==1)
            for s in sites for k in ('d','p')]
        fleet=model.addConstr(gp.quicksum(variables)<=problem.fleet.e1_count)
        if time.perf_counter()<deadline:
            model.Params.TimeLimit=max(0,deadline-time.perf_counter());model.optimize()
            if model.Status==gp.GRB.OPTIMAL:
                pi=[row.Pi for row in cover];nu=min(0.0,fleet.Pi)
                reduced=min([0.0]+[r['cost']-math.fsum(pi[i] for i in range(len(pi))
                    if (sites[i//2],('d','p')[i%2]) in r['tasks'])-nu for r in routes])
                dual=math.fsum(pi)+problem.fleet.e1_count*nu
                norm=math.fsum(abs(v) for v in pi)+abs(nu)
                margin=1e-7*max(1,abs(model.ObjVal),norm)*(1+2*len(sites)+problem.fleet.e1_count)
                # Repair the fleet dual using the minimum RAW reduced cost
                # over the complete compressed universe before certification.
                certified=dual+problem.fleet.e1_count*reduced-margin
                output.update(bound=max(fallback,certified),status='LP_OPTIMAL',
                    objective=model.ObjVal,margin=margin,relaxation_complete=True,
                    cover_duals=pi,fleet_dual=nu,minimum_reduced_cost=reduced,
                    dual_objective=dual,certified_cover_bound=certified,routes=routes)
            else:output['status']='MASTER_LIMIT_OR_INFEASIBLE'
        else:output['status']='MASTER_BUILD_LIMIT'
    finally:model.dispose()
    output['runtime']=time.perf_counter()-begun
    return output
