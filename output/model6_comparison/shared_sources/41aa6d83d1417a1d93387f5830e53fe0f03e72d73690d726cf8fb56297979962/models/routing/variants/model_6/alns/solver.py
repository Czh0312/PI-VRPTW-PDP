"""Adaptive large-neighborhood search on order batches and full cycle routes.

The shared physical decoder is a schedule propagation routine, not a call to
another numbered solver. Every batch change rebuilds demand and downstream
inventory. No exact backend or Gurobi is loaded.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import math
import random
import time

from ..model import validate_problem
from models.routing.common.cycle.joint import joint_empty_result, lift_result
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.alns.evaluator import initial_state, evaluate
from models.routing.solvers.alns.construction import chronological_state, appointment_state
from models.routing.solvers.alns.neighborhoods import clone_state, state_key, destroy_repair, _route_score


def _delay_departures(problem, result):
    """Move pre-service waiting to the satellite, preserving every event/end.

    A later departure cannot reduce supply or vehicle readiness. All customer
    starts, returns, cutoff bins and clean releases remain fixed. This removes
    avoidable paid route time without changing the causal task graph.
    """
    for trip in result['e2_trips']:
        first = trip['events'][0]
        customer = problem.order_map[first['order_id']].customer
        latest = first['start']-problem.travel(trip['satellite_id'],customer)
        trip['departure'] = max(trip['departure'],latest)


class JointEvaluator:
    def __init__(self, problem, capacity=512):
        self.problem, self.capacity = problem, capacity
        self.cache=OrderedDict()
        self.projections=OrderedDict()
        self.hits=self.misses=0
        # Feasibility penalty is search-only and scaled to declared cost units.
        rates=problem.cost_profile.rates
        self.penalty=max(1.,sum(rates.values()))*max(1,problem.total)*max(1,problem.operating_policy.minutes(problem.operating_policy.cycle_end))*100

    def fixed(self, assignment):
        key=tuple(sorted(assignment.items()))
        if key not in self.projections:
            self.projections[key]=self.problem.project(assignment,self.problem.total)
            if len(self.projections)>64:
                self.projections.popitem(last=False)
        return self.projections[key]

    def __call__(self, assignment, state):
        key=(tuple(sorted(assignment.items())),state_key(state),self.problem.cost_profile.hash)
        if key in self.cache:
            self.hits+=1
            self.cache.move_to_end(key)
            return self.cache[key]
        self.misses+=1
        fixed=self.fixed(assignment)
        _,raw,issues=evaluate(fixed,state)
        distance=raw['info'].get('objective') or 0.
        fallback=distance*max(self.problem.cost_profile.rates['e1_distance_cost_per_km'],
                              self.problem.cost_profile.rates['e2_distance_cost_per_km'],1.)
        if issues:
            value=(fallback+self.penalty*len(issues),None,tuple(issues))
        else:
            _delay_departures(fixed,raw)
            physical=audit_solution(fixed,raw)
            if not physical['valid']:
                value=(fallback+self.penalty*max(1,len(physical['errors'])),None,
                       tuple(e['code'] for e in physical['errors']))
            else:
                pool=max(self.problem.pool_min,physical['minimum_pool_for_schedule'])
                cost=self.problem.cost_profile.evaluate(physical,pool)['total']
                if pool>self.problem.pool_max:
                    value=(cost+self.penalty*(pool-self.problem.pool_max),None,('POOL_BOUNDS',))
                else:
                    candidate=lift_result(self.problem,raw,assignment,pool,'alns')
                    if candidate['audit']['valid']:
                        value=(candidate['info']['objective'],candidate,())
                    else:
                        value=(cost+self.penalty,None,('JOINT_AUDIT_FAILED',))
        self.cache[key]=value
        if len(self.cache)>self.capacity:
            self.cache.popitem(last=False)
        return value


def _best_construction(problem, evaluator, assignment, rng, deadline, *, randomized=False):
    fixed=evaluator.fixed(assignment)
    best=None
    for construct in (initial_state,appointment_state,chronological_state):
        if time.perf_counter()>=deadline and best is not None:
            break
        state=(construct(fixed,rng,randomized=randomized) if construct is initial_state else construct(fixed,rng))
        score,result,issues=evaluator(assignment,state)
        if best is None or score<best[0]:
            best=(score,state,result,issues)
    return best


def _batch_move(problem, evaluator, assignment, rng, operator, deadline):
    proposed=dict(assignment)
    ids=list(proposed)
    if operator=='close_batch':
        chosen=rng.choice(sorted(set(proposed.values())))
        ids=[oid for oid in ids if proposed[oid]==chosen]
    elif operator=='split_batch':
        chosen=rng.choice(sorted(set(proposed.values())))
        members=[oid for oid in ids if proposed[oid]==chosen]
        ids=rng.sample(members,max(1,len(members)//2))
    elif operator=='destroy_related_batch':
        anchor=problem.order_map[rng.choice(ids)]
        ids.sort(key=lambda oid:(problem.order_map[oid].satellite!=anchor.satellite,
                                problem.distance(anchor.customer,problem.order_map[oid].customer)))
        ids=ids[:min(len(ids),max(2,min(5,len(ids)//3)))]
    else:
        ids=rng.sample(ids,min(len(ids),max(1,min(4,len(ids)//3))))
    rng.shuffle(ids)
    built=None
    for oid in ids:
        if time.perf_counter()>=deadline:
            break
        choices=[b for b in problem.legal_batches[oid] if b!=proposed[oid]]
        if not choices:
            continue
        rng.shuffle(choices)
        best=None
        for bid in choices:
            if time.perf_counter()>=deadline:
                break
            trial={**proposed,oid:bid}
            construction=_best_construction(problem,evaluator,trial,rng,deadline)
            if best is None or construction[0]<best[0]:
                best=(construction[0],trial,construction)
        if best is not None:
            _,proposed,built=best
    if built is None:
        built=_best_construction(problem,evaluator,proposed,rng,deadline)
    return proposed,built[1]


def _route_move(problem, state, rng, operator):
    proposed=clone_state(state)
    routes=proposed['e2']
    f,p=problem.fleet,problem.operating_policy
    if operator in ('relocate','pair') and routes:
        source=rng.choice(routes)
        oid,kind=rng.choice(source['tasks'])
        items=[(oid,'d'),(oid,'p')] if operator=='pair' else [(oid,kind)]
        for r in routes:
            r['tasks']=[t for t in r['tasks'] if t not in items]
        routes[:]=[r for r in routes if r['tasks']]
        order=problem.order_map[oid]
        candidates=[r for r in routes if r['satellite']==order.satellite and
                    (not any(k=='d' for _,k in items) or all(k!='d' or problem.order_map[o].batch==order.batch for o,k in r['tasks']))]
        if not candidates or rng.random()<.2:
            target=dict(satellite=order.satellite,vehicle=rng.randrange(f.e2_per_sat_count),tasks=[])
            routes.append(target)
        else:
            target=rng.choice(candidates)
        position=rng.randrange(len(target['tasks'])+1)
        target['tasks'][position:position]=items
    elif operator=='split_merge' and routes:
        source=rng.choice(routes)
        others=[r for r in routes if r is not source and r['satellite']==source['satellite']]
        if len(source['tasks'])>1 and (not others or rng.random()<.5):
            i=rng.randrange(1,len(source['tasks']))
            routes.insert(routes.index(source)+1,dict(satellite=source['satellite'],
                          vehicle=rng.randrange(f.e2_per_sat_count),tasks=source['tasks'][i:]))
            source['tasks']=source['tasks'][:i]
        elif others:
            other=rng.choice(others); source['tasks']+=other['tasks']; routes.remove(other)
    elif operator=='vehicle' and routes:
        if rng.random()<.5:
            rng.choice(routes)['vehicle']=rng.randrange(f.e2_per_sat_count)
        elif len(routes)>1:
            a,b=rng.sample(range(len(routes)),2); routes[a],routes[b]=routes[b],routes[a]
    elif operator=='wait' and routes:
        r=rng.choice(routes)
        boundary=rng.choice(p.batches)
        t=rng.choice([p.cycle_start,boundary.cutoff,boundary.cutoff+1,boundary.start])
        r[rng.choice(['depart_after','return_after'])]=min(t,p.cycle_end)
    elif operator=='e1_delivery':
        rows=proposed['deliveries'][rng.choice(p.batches).id]
        nonempty=[r for r in rows if r]
        if nonempty:
            source=rng.choice(nonempty); site=source.pop(rng.randrange(len(source)))
            target=rng.choice(rows); target.insert(rng.randrange(len(target)+1),site)
    elif operator=='e1_pickup':
        b=rng.choice(p.batches)
        rng.shuffle(proposed['pickup_order'][b.id])
        proposed['pickup_vehicle'][b.id][rng.choice(problem.satellites)]=rng.randrange(f.e1_count)
    elif operator=='reverse' and routes:
        r=rng.choice(routes)
        if len(r['tasks'])>=2:
            a,b=sorted(rng.sample(range(len(r['tasks'])),2))
            r['tasks'][a:b+1]=reversed(r['tasks'][a:b+1])
    return proposed


def _local_cost_scorer(problem, route, goods, ready):
    """Insertion surrogate; only complete audited cost ranks final solutions."""
    score,end=_route_score(problem,route,goods,ready)
    ids=[route['satellite']]+[problem.order_map[o].customer for o,_ in route['tasks']]+[route['satellite']]
    distance=sum(problem.distance(a,b) for a,b in zip(ids,ids[1:]))
    delivered=[problem.order_map[o].batch for o,k in route['tasks'] if k=='d']
    start=max(problem.operating_policy.cycle_start,ready,route.get('depart_after',0))
    if delivered:
        start=max(start,max(goods[route['satellite'],b] for b in delivered))+problem.operating_policy.e2_dispatch_prep_time
    rates=problem._joint_cost_rates
    return (distance*rates['distance_scale_to_km']*rates['e2_distance_cost_per_km']
            +problem.operating_policy.minutes(max(0,end-start)+
                (problem.operating_policy.e2_dispatch_prep_time if delivered else 0))*rates['e2_work_cost_per_minute']
            +rates['e2_trip_cost']+max(0,score-distance)),end


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42):
    validate_problem(problem)
    if (isinstance(max_time,bool) or not math.isfinite(max_time) or max_time<=0
            or isinstance(max_iterations,bool) or not isinstance(max_iterations,int) or max_iterations<0):
        raise ValueError('Invalid ALNS time or iteration budget')
    begun=time.perf_counter(); deadline=begun+max_time
    missing=[oid for oid,choices in problem.legal_batches.items() if not choices]
    if missing:
        result=joint_empty_result(problem,'alns')
        result['info'].update(status='INFEASIBLE',proven_infeasible=True,
                              proof_scope='empty_necessary_order_batch_domain',orders=missing)
        return result
    rng=random.Random(seed); evaluator=JointEvaluator(problem)
    original={o.id:o.batch if o.batch in problem.legal_batches[o.id] else problem.legal_batches[o.id][0] for o in problem.orders}
    earliest={oid:min(bs,key=lambda b:problem.batch_index[b]) for oid,bs in problem.legal_batches.items()}
    latest={oid:max(bs,key=lambda b:problem.batch_index[b]) for oid,bs in problem.legal_batches.items()}
    seeds=[original,earliest,latest]
    for b in problem.operating_policy.batches:
        seeds.append({oid:b.id if b.id in bs else earliest[oid] for oid,bs in problem.legal_batches.items()})
    current=None; best=None; best_state=None; best_assignment=None; best_cost=math.inf
    seen=set(); history=[]
    for assignment in seeds:
        if current is not None and time.perf_counter()>=deadline:
            break
        score,state,result,issues=_best_construction(problem,evaluator,assignment,rng,deadline)
        seen.update(issues)
        if current is None or score<current[0]:
            current=(score,assignment,state)
        if result is not None and score<best_cost:
            best,best_cost=deepcopy(result),score
            best_assignment,best_state=dict(assignment),clone_state(state)
            history.append(dict(iteration=0,objective=score,time=time.perf_counter()-begun))
    operators=['destroy_random_batch','destroy_related_batch','close_batch','split_batch',
               'destroy_routes','relocate','pair','split_merge','vehicle','wait',
               'e1_delivery','e1_pickup','reverse']
    weights=[1.]*len(operators); rewards=[0.]*len(operators); uses=[0]*len(operators)
    attempts={op:0 for op in operators}; improvements={op:0 for op in operators}
    accepted=iterations=restarts=0
    score,assignment,state=current
    for iteration in range(max_iterations):
        if time.perf_counter()>=deadline:
            break
        iterations=iteration+1
        index=rng.choices(range(len(operators)),weights=weights)[0]
        op=operators[index]; attempts[op]+=1; uses[index]+=1
        if iteration and iteration%100==0 and best is not None:
            assignment,state=dict(best_assignment),clone_state(best_state)
            score=evaluator(assignment,state)[0]; restarts+=1
        proposed_assignment=dict(assignment)
        if 'batch' in op:
            proposed_assignment,proposed=_batch_move(problem,evaluator,assignment,rng,op,deadline)
        else:
            fixed=evaluator.fixed(assignment)
            if op=='destroy_routes':
                fixed._joint_cost_rates=problem.cost_profile.rates
                proposed=destroy_repair(fixed,state,rng,rng.choice(['random','related']),deadline,
                                        scorer=_local_cost_scorer)
            else:
                proposed=_route_move(fixed,state,rng,op)
        if time.perf_counter()>=deadline:
            break
        new_score,candidate,issues=evaluator(proposed_assignment,proposed)
        seen.update(issues); reward=0.
        if candidate is not None and new_score<best_cost-1e-8:
            best,best_cost=deepcopy(candidate),new_score
            best_assignment,best_state=dict(proposed_assignment),clone_state(proposed)
            history.append(dict(iteration=iterations,objective=best_cost,time=time.perf_counter()-begun))
            improvements[op]+=1; reward=8.
        fraction=min(1.,(time.perf_counter()-begun)/max_time)
        temperature=max(.000001,max(1.,best_cost if math.isfinite(best_cost) else score)*.04*(1-fraction))
        if new_score<=score or rng.random()<math.exp(max(-700.,(score-new_score)/temperature)):
            if new_score<score:
                reward=max(reward,3.)
            else:
                reward=max(reward,1.)
            score,assignment,state=new_score,proposed_assignment,proposed
            accepted+=1
        rewards[index]+=reward
        if iterations%25==0:
            weights=[max(.1,.8*w+.2*(r/u if u else w)) for w,r,u in zip(weights,rewards,uses)]
            rewards=[0.]*len(operators); uses=[0]*len(operators)
    result=best if best is not None else joint_empty_result(problem,'alns')
    result['solver_module']=__name__
    result['info'].update(status='FEASIBLE' if best is not None else 'HEURISTIC_NO_SOLUTION',
                          proven_optimal=False,proven_infeasible=False,best_bound=None,mip_gap=None,
                          runtime=time.perf_counter()-begun,iterations=iterations,seed=seed,
                          accepted_moves=accepted,restarts=restarts,operator_attempts=attempts,
                          operator_improvements=improvements,operator_weights=dict(zip(operators,weights)),
                          cache_hits=evaluator.hits,evaluations=evaluator.misses,
                          encountered_violations=sorted(seen),history=history,
                          decoder_scope='earliest_DAG_events_plus_explicit_waits_and_latest_departure_before_first_service')
    if best is not None:
        result['audit']=audit_solution(problem,result)
        if not result['audit']['valid']:
            result['info'].update(status='AUDIT_FAILED',objective=None)
    return result
