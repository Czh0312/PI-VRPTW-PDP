"""Audited upper bounds by reassigning fixed E2 paths to physical vehicles.

Task sequences and their load curves stay fixed; E1 routes, actual E2 clocks,
vehicle assignments, and per-vehicle order may change. Pairwise disjunctions
include preparation and turnaround. Event ranks retain causal order even
when travel and service have zero duration. This is a restricted primal
subproblem, so its bound and optimality NEVER prove full M5 optimality.
"""
from collections import defaultdict
from copy import deepcopy
import heapq
import math
from pathlib import Path
import time

from models.routing.common.cycle.audit import audit_solution


def build_vehicle_retiming(problem, initial, *, deadline=None, verbose=False, backend='auto'):
    import gurobipy as gp
    from .multibatch_e1_routes import build_e1_routes
    from .multibatch_e1_inventory import build_e1_inventory
    if (problem.model_number != 5 or problem.free_assignment or not problem.reusable
            or len(problem.operating_policy.batches) <= 1):
        raise ValueError('Vehicle retiming requires fixed reusable multibatch M5')
    if not audit_solution(problem, initial)['valid']:
        raise ValueError('Initial schedule must pass the full M5 audit')
    if backend not in ('auto', 'patterns', 'indexed'):
        raise ValueError('backend must be auto, patterns or indexed')
    pattern_scope = len({o.satellite for o in problem.orders}) <= 4 and problem.operating_policy.e1_turnaround_time == 0
    chosen = ('patterns' if pattern_scope else 'indexed') if backend == 'auto' else backend
    if chosen == 'patterns' and not pattern_scope:
        raise ValueError('Pattern upper bounds require <=4 sites and zero E1 turnaround')
    builder = build_e1_routes if chosen == 'patterns' else build_e1_inventory
    model, context = builder(problem, deadline=deadline, verbose=verbose)
    try:
        def check():
            if deadline is not None and time.perf_counter() >= deadline:
                raise TimeoutError('Vehicle retiming reached its total budget while building')
        p, fleet = problem.operating_policy, problem.fleet
        supplies = context.get('supplies', context.get('deliveries'))
        trips = initial['e2_trips']
        by_site = defaultdict(list)
        for i, trip in enumerate(trips): by_site[trip['satellite_id']].append(i)
        node_counts = {s:sum(len(trips[i]['events'])+2 for i in indices) for s,indices in by_site.items()}
        timings, ranks, tasks, prep, assignments, before = {}, {}, {}, {}, {}, {}
        fixed_cost = 0.0
        for i, trip in enumerate(trips):
            check()
            site = trip['satellite_id']
            events = trip['events']
            prepare = p.e2_dispatch_prep_time if any(e['kind']=='d' for e in events) else 0
            start = model.addVar(vtype=gp.GRB.INTEGER, lb=p.cycle_start+prepare, ub=p.cycle_end,
                                 name=f'fleet_departure_{i}')
            end = model.addVar(vtype=gp.GRB.INTEGER, lb=p.cycle_start, ub=p.cycle_end-p.e2_turnaround_time,
                               name=f'fleet_return_{i}')
            if any(e['kind']=='p' for e in events): end.UB = min(p.batches[-1].cutoff,p.cycle_end-p.e2_turnaround_time)
            timings[i] = start,end
            prep[i] = prepare
            for k in range(-1,len(events)+1):
                ranks[i,k] = model.addVar(vtype=gp.GRB.INTEGER, ub=node_counts[site]-1,
                                         name=f'fleet_rank_{i}_{k}')
            for k in range(len(events)+1):
                model.addConstr(ranks[i,k] >= ranks[i,k-1]+1, name=f'fleet_rank_path_{i}_{k}')
            clock, previous = start, site
            places = [site]
            for k,event in enumerate(events):
                order = problem.order_map[event['order_id']]
                at = context['order_times'][order.id,event['kind']]
                model.addConstr(at >= clock+problem.travel(previous,order.customer),
                                name=f'fleet_travel_{i}_{k}')
                if event['kind']=='d':
                    model.addConstr(start >= supplies[site,order.batch]+prepare,
                                    name=f'fleet_supply_{i}_{k}')
                else:
                    model.addConstr(context['returns'][order.id] == end,
                                    name=f'fleet_common_return_{i}_{k}')
                tasks[order.id,event['kind']] = i,k
                clock,previous = at+order.service_time,order.customer
                places.append(order.customer)
            model.addConstr(end >= clock+problem.travel(previous,site)+p.e2_return_unload_time,
                            name=f'fleet_end_{i}')
            places.append(site)
            fixed_cost += math.fsum(problem.distance(a,b) for a,b in zip(places,places[1:]))
            for vehicle in range(fleet.e2_per_sat_count):
                assignments[i,vehicle] = model.addVar(vtype=gp.GRB.BINARY,name=f'fleet_assign_{i}_{vehicle}')
            model.addConstr(gp.quicksum(assignments[i,v] for v in range(fleet.e2_per_sat_count)) == 1,
                            name=f'fleet_exact_assignment_{i}')
        for order in problem.orders:
            model.addConstr(ranks[tasks[order.id,'p']] >= ranks[tasks[order.id,'d']]+1,
                            name=f'fleet_rank_use_{order.id}')
        for site,indices in by_site.items():
            check()
            N, horizon = node_counts[site], p.cycle_end-p.cycle_start
            for vehicle in range(fleet.e2_per_sat_count):
                model.addConstr(gp.quicksum(assignments[i,vehicle] for i in indices) <= p.e2_max_trips_per_vehicle,
                                name=f'fleet_trip_limit_{site}_{vehicle}')
            for pos,i in enumerate(indices):
                for j in indices[pos+1:]:
                    check()
                    forward = model.addVar(vtype=gp.GRB.BINARY,name=f'fleet_before_{i}_{j}')
                    before[i,j] = forward
                    for vehicle in range(fleet.e2_per_sat_count):
                        a,b = assignments[i,vehicle],assignments[j,vehicle]
                        off_forward,off_backward = 3-a-b-forward,2-a-b+forward
                        model.addConstr(timings[j][0]-prep[j] >= timings[i][1]+p.e2_turnaround_time-horizon*off_forward,
                                        name=f'fleet_forward_{i}_{j}_{vehicle}')
                        model.addConstr(timings[i][0]-prep[i] >= timings[j][1]+p.e2_turnaround_time-horizon*off_backward,
                                        name=f'fleet_backward_{i}_{j}_{vehicle}')
                        model.addConstr(ranks[j,-1] >= ranks[i,len(trips[i]['events'])]+1-N*off_forward,
                                        name=f'fleet_rank_forward_{i}_{j}_{vehicle}')
                        model.addConstr(ranks[i,-1] >= ranks[j,len(trips[j]['events'])]+1-N*off_backward,
                                        name=f'fleet_rank_backward_{i}_{j}_{vehicle}')
        model.setObjective(model.getObjective()+fixed_cost)
        model.update()
        check()
        context.update(e2_timing=timings,e2_assignment=assignments,e2_before=before,e2_ranks=ranks,
            e2_tasks=tasks,e2_prep=prep,e2_by_site=dict(by_site),fixed_e2_cost=fixed_cost,vehicle_backend=chosen,
            fixed_sequences=tuple((t['satellite_id'],tuple((e['order_id'],e['kind']) for e in t['events']))
                                  for t in trips))
        set_vehicle_projection(problem,context,initial)
        return model,context
    except Exception:
        model.dispose()
        raise


def set_vehicle_projection(problem, context, initial, *, fix=False):
    """Project an audited full schedule with the SAME fixed task sequences."""
    from .multibatch_e1_routes import set_route_projection
    from .multibatch_e1_inventory import set_e1_projection
    if not audit_solution(problem,initial)['valid']:
        raise ValueError('Vehicle projection requires a valid full schedule')
    if tuple((t['satellite_id'],tuple((e['order_id'],e['kind']) for e in t['events']))
             for t in initial['e2_trips']) != context['fixed_sequences']:
        raise ValueError('Vehicle projection requires the same fixed task sequences')
    project = set_route_projection if context['vehicle_backend']=='patterns' else set_e1_projection
    project(problem,context,initial,fix=fix)
    def put(var,value):
        var.Start=value
        if fix:var.LB=var.UB=value
    trips=initial['e2_trips']
    graph,indegree=defaultdict(list),{key:0 for key in context['e2_ranks']}
    def edge(a,b):graph[a].append(b);indegree[b]+=1
    for i,trip in enumerate(trips):
        start,end=context['e2_timing'][i]
        put(start,trip['departure']);put(end,trip['return_complete'])
        for k,event in enumerate(trip['events']):
            put(context['order_times'][event['order_id'],event['kind']],event['start'])
            if event['kind']=='p':put(context['returns'][event['order_id']],trip['return_complete'])
            edge((i,k-1),(i,k))
        edge((i,len(trip['events'])-1),(i,len(trip['events'])))
        for v in range(problem.fleet.e2_per_sat_count):put(context['e2_assignment'][i,v],int(v==trip['vehicle_id']))
    for order in problem.orders:edge(context['e2_tasks'][order.id,'d'],context['e2_tasks'][order.id,'p'])
    grouped=defaultdict(list)
    for i,t in enumerate(trips):grouped[t['satellite_id'],t['vehicle_id']].append(i)
    for indices in grouped.values():
        indices.sort(key=lambda i:trips[i]['trip_index'])
        for i,j in zip(indices,indices[1:]):edge((i,len(trips[i]['events'])),(j,-1))
    # Each site has its own rank range. A topological order gives valid starts
    # even for zero-duration operations and inter-trip customer precedence.
    values={}
    for site,indices in context['e2_by_site'].items():
        nodes={key for key in indegree if key[0] in indices}
        ready=[key for key in nodes if indegree[key]==0];heapq.heapify(ready)
        counter=0
        while ready:
            node=heapq.heappop(ready);values[node]=counter;counter+=1
            for other in graph[node]:
                indegree[other]-=1
                if indegree[other]==0:heapq.heappush(ready,other)
        if counter!=len(nodes):raise ValueError('Fixed sequences and physical vehicle order contain a causal cycle')
    for key,var in context['e2_ranks'].items():put(var,values[key])
    for (i,j),var in context['e2_before'].items():put(var,int(values[i,-1]<values[j,-1]))


def reoptimize_vehicle_retiming(problem, initial_result, *, time_limit=10, seed=42,
                               verbose=False, backend='auto', diagnostics_path=None):
    """Return only an audited full upper; all proof flags remain False."""
    import gurobipy as gp
    from .multibatch_e1_polish import extract_routes
    if not math.isfinite(time_limit) or time_limit<=0:
        raise ValueError('time_limit must be finite and positive')
    audit=audit_solution(problem,initial_result)
    if not audit['valid']:raise ValueError('Initial schedule must pass the full M5 audit')
    begun,model=time.perf_counter(),None
    result=deepcopy(initial_result)
    for field in ('audit','cutoff_snapshots','depot_releases'):result.pop(field,None)
    info=dict(status='FEASIBLE',objective=audit['objective'],best_bound=None,mip_gap=None,
        proven_optimal=False,proven_infeasible=False,
        proof_scope='audited_fixed_E2_sequences_flexible_vehicle_retiming',
        upper_before=audit['objective'],subproblem_optimal=False,subproblem_lower_bound=None,
        vehicle_retiming_status='TIME_LIMIT_BUILD',time_limit_seconds=time_limit)
    try:
        model,context=build_vehicle_retiming(problem,initial_result,deadline=begun+time_limit,
                                            verbose=verbose,backend=backend)
        model.Params.Seed=int(seed)
        model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9
        model.Params.TimeLimit=max(0,begun+time_limit-time.perf_counter())
        model.optimize()
        try:bound=float(model.ObjBound)
        except (gp.GurobiError,AttributeError):bound=math.nan
        info.update(vehicle_retiming_status=int(model.Status),vehicle_backend=context['vehicle_backend'],
            subproblem_optimal=model.Status==gp.GRB.OPTIMAL,
            subproblem_lower_bound=bound if math.isfinite(bound) else None,
            variables=model.NumVars,constraints=model.NumConstrs,nodes=model.NodeCount)
        if model.SolCount:
            candidate=extract_routes(problem,context,context['vehicle_backend'],initial_result['e2_trips'])
            assigned=defaultdict(list)
            for i,trip in enumerate(candidate['e2_trips']):
                vehicles=[v for v in range(problem.fleet.e2_per_sat_count) if context['e2_assignment'][i,v].X>.5]
                if len(vehicles)!=1:raise RuntimeError('Vehicle assignment is not unique')
                assigned[trip['satellite_id'],vehicles[0]].append(i)
            for (_,vehicle),indices in assigned.items():
                indices.sort(key=lambda i:context['e2_ranks'][i,-1].X)
                for slot,i in enumerate(indices):
                    candidate['e2_trips'][i]['vehicle_id']=vehicle
                    candidate['e2_trips'][i]['trip_index']=slot
            checked=audit_solution(problem,candidate)
            if not checked['valid'] or not math.isclose(checked['objective'],model.ObjVal,rel_tol=1e-7,abs_tol=1e-5):
                raise RuntimeError(f'Vehicle retiming failed complete schedule audit: {checked["errors"]}')
            if checked['objective']<audit['objective']-1e-6:result,audit=candidate,checked
        if diagnostics_path is not None:
            path=Path(diagnostics_path);path.parent.mkdir(parents=True,exist_ok=True);model.write(str(path))
    except TimeoutError:
        pass
    finally:
        if model is not None:model.dispose()
    info.update(objective=audit['objective'],improved=audit['objective']<info['upper_before']-1e-6,
        vehicles_changed=[(t['vehicle_id'],t['trip_index']) for t in result['e2_trips']] !=
                         [(t['vehicle_id'],t['trip_index']) for t in initial_result['e2_trips']],
        runtime=time.perf_counter()-begun)
    result['info']=info
    result['audit']=audit_solution(problem,result)
    if not result['audit']['valid']:raise RuntimeError('Returned vehicle-retiming schedule is invalid')
    return result
