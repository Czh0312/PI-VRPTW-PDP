"""Complete B1 M3 E1 route-cover relaxation; never an integer proof.

Original E1 geometry explicitly forbids pickup-to-delivery edges. Relaxing
clocks, E2 synchronization and stocks leaves two independent maximum loads:
the sum of deliveries at departure and the sum of pickups at return. Thus
routes with the same tasks have identical coefficients and feasibility, and
only their cheapest RAW directed path is needed. Interrupted enumeration
does not certify any restricted-pool objective. The optional clocked universe
checks every raw path before minimum-cost compression, retaining original
E1 necessary windows, integer clocks and cutoff/return rules.
"""
from collections import Counter
from copy import deepcopy
import itertools
import math
import time

_RELAXED_SCOPE='B1_M3_E1_complete_clock_relaxed_cover_LP'
_CLOCKED_SCOPE='B1_M3_E1_complete_clocked_cover_LP'


def _clocked_flag(clocked):
    if not isinstance(clocked,bool):raise ValueError('clocked must be boolean')


def _clocked_replay(problem,tasks,windows):
    """Earliest integer E1 clocks with original RAW arc travel and loads."""
    p,f=problem.operating_policy,problem.fleet;b=p.batches[0]
    if not tasks or len(set(tasks))!=len(tasks):return None
    if any(k=='p' and l=='d' for (_,k),(_,l) in zip(tasks,tasks[1:])):return None
    weight=math.fsum(problem.weights[s,b.id] for s,k in tasks if k=='d')
    volume=sum(problem.demands[s,b.id]*f.container_volume for s,k in tasks if k=='d')
    if weight>f.e1_weight_cap+1e-6 or volume>f.e1_volume_cap+1e-6:return None
    departure=b.start+p.e1_load_time if any(k=='d' for s,k in tasks) else b.cutoff
    clock=departure;previous=problem.depot;previous_kind=None;starts=[];costs=[]
    for site,kind in tasks:
        travel=problem.travel(previous,site);arrival=clock+travel
        if kind=='p' and previous_kind in (None,'d'):arrival=max(arrival,b.cutoff+travel)
        low,high=windows[site,kind];start=max(low,arrival)
        if start>high:return None
        starts.append(start);costs.append(problem.distance(previous,site));clock=start+problem.service(site)
        weight+=problem.demands[site,b.id]*f.container_weight if kind=='p' else -problem.weights[site,b.id]
        volume+=(1 if kind=='p' else -1)*problem.demands[site,b.id]*f.container_volume
        if not(-1e-6<=weight<=f.e1_weight_cap+1e-6 and -1e-6<=volume<=f.e1_volume_cap+1e-6):return None
        previous,previous_kind=site,kind
    returned=clock+problem.travel(previous,problem.depot)+p.depot_return_unload_time
    latest=min(b.end,p.cycle_end-p.e1_turnaround_time,
        p.cycle_end-p.cleaning_duration if any(k=='p' for s,k in tasks) else p.cycle_end)
    if returned>latest:return None
    costs.append(problem.distance(previous,problem.depot))
    return dict(tasks=tuple(tasks),starts=starts,departure=departure,return_complete=returned,cost=math.fsum(costs))


def _enumerate_clocked_routes(problem,sites,deadline,max_sites):
    from models.common.cycle.precheck import travel_lower_bounds
    from models.routing.model_3.exact.accelerators.strengthening import single_batch_bounds
    n=len(sites);examined=0;feasible=0;groups={};index={s:i for i,s in enumerate(sites)}
    if n>max_sites:return [],dict(complete=False,status='SITE_LIMIT',sites=sites,paths=0)
    def check():
        if deadline is not None and time.perf_counter()>=deadline:raise TimeoutError
    try:
        check();windows=single_batch_bounds(problem,travel_lower_bounds(problem))['visits'];check()
        parts=[]
        for size in range(n+1):
            for sequence in itertools.permutations(sites,size):check();parts.append(sequence)
        for ds in parts:
            for ps in parts:
                check()
                if not ds and not ps:continue
                examined+=1;tasks=tuple((s,'d') for s in ds)+tuple((s,'p') for s in ps)
                route=_clocked_replay(problem,tasks,windows)
                if route is None:continue
                feasible+=1;mask=sum(1<<(index[s]+(n if k=='p' else 0)) for s,k in tasks)
                route['mask']=mask;old=groups.get(mask)
                if old is None or (route['cost'],tasks)<(old['cost'],old['tasks']):groups[mask]=route
        check()
        return [groups[k] for k in sorted(groups)],dict(complete=True,status='COMPLETE',sites=sites,
            paths=feasible,examined=examined,clock_feasible=feasible)
    except TimeoutError:return [],dict(complete=False,status='ENUMERATION_LIMIT',sites=sites,paths=feasible,examined=examined)

def _sites(problem):
    if problem.model_number!=3 or len(problem.operating_policy.batches)!=1:
        raise ValueError('E1 route cover requires B1 M3')
    batch=problem.operating_policy.batches[0]
    return [s for s in problem.satellites if problem.demands[s,batch.id]]


def enumerate_e1_routes(problem,*,deadline=None,max_sites=5,clocked=False):
    """Complete minimum-cost representative for every feasible task subset."""
    _clocked_flag(clocked)
    sites=_sites(problem);n=len(sites);f=problem.fleet;b=problem.operating_policy.batches[0]
    if clocked:return _enumerate_clocked_routes(problem,sites,deadline,max_sites)
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


def project_e1_schedule(problem,result,routes,*,clocked=False):
    """Original trip projects unchanged before same-cover cost dominance."""
    from models.common.cycle.audit import audit_solution
    _clocked_flag(clocked)
    sites=_sites(problem);n=len(sites);index={s:i for i,s in enumerate(sites)}
    representatives={r['mask']:r for r in routes};audit=audit_solution(problem,result)
    if not audit['valid'] or not audit['periodic_replay']['valid']:
        raise ValueError('E1 projection requires audited original integer schedule')
    records=[];coverage=Counter();batch=problem.operating_policy.batches[0];windows=None
    if clocked:
        from models.common.cycle.precheck import travel_lower_bounds
        from models.routing.model_3.exact.accelerators.strengthening import single_batch_bounds
        windows=single_batch_bounds(problem,travel_lower_bounds(problem))['visits']
    for trip in result['e1_trips']:
        tasks=tuple((e['site_id'],e['kind']) for e in trip['events'])
        if any(k=='p' and next_kind=='d' for (_,k),(_,next_kind) in zip(tasks,tasks[1:])):
            raise RuntimeError('Original E1 violated delivery-first geometry')
        if len(set(tasks))!=len(tasks):raise RuntimeError('Repeated original warehouse task')
        if clocked:
            replay=_clocked_replay(problem,tasks,windows)
            if replay is None or replay['departure']>trip['departure'] or replay['return_complete']>trip['return_complete']:
                raise RuntimeError('Original E1 clock schedule failed projection')
            if any(t>e['start'] for t,e in zip(replay['starts'],trip['events'])):
                raise RuntimeError('Relaxed E1 clock exceeds original service')
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


def e1_route_cover_bound(problem,time_limit=2,*,seed=42,max_sites=5,clocked=False):
    """Certified complete-universe LP floor; interrupted cases use old floor."""
    if not math.isfinite(time_limit) or time_limit<=0:raise ValueError('E1 budget must be finite and positive')
    _clocked_flag(clocked);sites=_sites(problem)
    from models.routing.model_3.exact.accelerators.single_batch.pickup_bound import e1_component_floor
    from models.routing.model_3.exact.accelerators.single_batch.columns import problem_fingerprint
    begun=time.perf_counter();deadline=begun+time_limit;fallback=e1_component_floor(problem)
    output=dict(proof_scope=_CLOCKED_SCOPE if clocked else _RELAXED_SCOPE,clocked=clocked,bound=fallback,fallback=fallback,
        problem_sha256=problem_fingerprint(problem),
        proven_optimal=False,proven_infeasible=False,relaxation_complete=False,sites=sites,
        original_physical_model_unchanged=True)
    routes,enumeration=enumerate_e1_routes(problem,deadline=deadline,max_sites=max_sites,clocked=clocked)
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


def apply_e1_cover_bound(model,problem,context,certificate,*,deadline=None):
    """Private hook for an independently validated matching E1 certificate."""
    from models.routing.model_3.exact.accelerators.single_batch.columns import problem_fingerprint
    _sites(problem)
    if not isinstance(certificate,dict) or certificate.get('proof_scope') not in (_RELAXED_SCOPE,_CLOCKED_SCOPE):
        raise ValueError('Wrong E1 certificate scope')
    if certificate.get('proof_scope')==_CLOCKED_SCOPE and certificate.get('clocked') is not True:
        raise ValueError('Clocked E1 certificate requires its clocked scope marker')
    if certificate.get('proof_scope')==_RELAXED_SCOPE and certificate.get('clocked',False) is not False:
        raise ValueError('Clock-relaxed E1 certificate has a conflicting scope marker')
    if certificate.get('problem_sha256')!=problem_fingerprint(problem):
        raise ValueError('E1 certificate does not match this problem')
    bound=certificate.get('bound')
    if isinstance(bound,bool) or not isinstance(bound,(int,float)) or not math.isfinite(bound) or bound<0:
        raise ValueError('E1 certificate bound must be finite and nonnegative')
    if deadline is not None and time.perf_counter()>=deadline:
        return dict(bound=float(bound),applied=False,status='APPLICATION_LIMIT')
    import gurobipy as gp
    expression=gp.LinExpr()
    for route in context['e1']:
        if deadline is not None and time.perf_counter()>=deadline:
            return dict(bound=float(bound),applied=False,status='APPLICATION_LIMIT')
        for (a,b),variable in route['x'].items():
            left=problem.depot if a<0 else route['nodes'][a]['site']
            right=problem.depot if b<0 else route['nodes'][b]['site']
            expression+=problem.distance(left,right)*variable
    if bound>0:model.addConstr(expression>=bound,name='B1_M3_certified_E1_distance_floor')
    return dict(bound=float(bound),applied=bound>0,status='APPLIED' if bound>0 else 'ZERO')


def solve_exact_with_e1_cover(problem,time_limit=60,seed=42,verbose=False,initial_result=None,*,clocked=False):
    """Explicit research solver: E1 component then original complete MILP.

    Enumeration, LP, fingerprint and application share the total budget. No
    route-pool restriction, changed business data or default enablement.
    """
    if not math.isfinite(time_limit) or time_limit<=0:raise ValueError('Total budget must be finite and positive')
    _clocked_flag(clocked);_sites(problem);begun=time.perf_counter()
    certificate=e1_route_cover_bound(problem,min(2.0,0.1*time_limit),seed=seed,clocked=clocked)
    stage=time.perf_counter()-begun;remaining=max(0.0,time_limit-stage)
    if remaining>0:
        from models.routing.model_3.exact.search.engine import solve_exact
        result=solve_exact(problem,remaining,seed,verbose,initial_result,
            _single_batch_e1_certificate=certificate)
    else:
        from models.common.cycle.audit import audit_solution
        from models.common.cycle.state import empty_result
        audit=audit_solution(problem,initial_result) if initial_result is not None else None
        result=deepcopy(initial_result) if audit and audit['valid'] else empty_result(problem,'exact')
        result['info'].update(status='TIME_LIMIT_E1_COMPONENT',proven_optimal=False,proven_infeasible=False,
            best_bound=None,mip_gap=None)
    info=result['info'];previous=info.get('best_bound')
    if not isinstance(previous,(int,float)) or not math.isfinite(previous):previous=0.0
    bound=max(previous,certificate['bound']);info['best_bound']=bound
    upper=info.get('objective')
    if upper is not None:
        info['mip_gap']=max(0.0,upper-bound)/max(1e-9,abs(upper))
    info.update(e1_cover_certificate=certificate,e1_cover_stage_runtime=stage,
        e1_cover_kernel_budget=remaining,runtime=time.perf_counter()-begun,
        best_bound_source='E1_component_and_complete_original_MILP')
    return result
