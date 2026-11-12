"""Adaptive destroy/repair search with annealing and container-peak repair."""
import math
import random
import time

from models.common.no_batch.audit import audit_solution
from models.common.no_batch.instance import VERSION
from .construction import initial_states
from .evaluator import evaluate, improve_pool
from .neighborhoods import DESTROY, REPAIR, destroy, repair, mutate


def solve_alns(problem,max_time=120,max_iterations=5000,seed=42,*,initial_result=None):
    if problem.model_number!=10:raise ValueError('M10 entry requires its own no-batch instance')
    if not math.isfinite(max_time) or max_time<=0 or max_iterations<0:
        raise ValueError('Invalid ALNS budget')
    begun=time.perf_counter();deadline=begun+max_time;rng=random.Random(seed)
    current=value=best=best_state=None
    weights={k:1. for k in DESTROY+REPAIR};uses={k:0 for k in weights};rewards={k:0. for k in weights}
    history=[];evaluations=0;accepted=0
    def key(v):
        return (round(v['cost'],10),v['pool']) if problem.objective_mode=='transport' else (v['pool'],round(v['cost'],10))
    def consider(s,v):
        nonlocal best,best_state
        if v and v['result']['feasible'] and (best is None or key(v)<key(best)):
            best,best_state=v,s.copy()
            history.append(dict(seconds=time.perf_counter()-begun,pool=v['pool'],transport_cost=v['cost']))
            return True
        return False
    def score(v):
        if not v:return math.inf
        penalty=max(0,v['pool']-problem.pool_max)*max(100.,v['cost']*10)
        if problem.transport_budget is not None:penalty+=max(0,v['cost']-problem.transport_budget)*100
        return (v['cost'] if problem.objective_mode=='transport' else v['pool']*10000+v['cost'])+penalty
    seeds=[]
    if initial_result is not None:
        from .warm_start import from_result
        seeds.append(from_result(problem,initial_result))
    for state in iter_seeds(seeds,problem):
        if time.perf_counter()>=deadline:break
        candidate=evaluate(problem,state);evaluations+=1
        if candidate:
            state,candidate=improve_pool(problem,state,candidate,deadline,attempts=20)
            consider(state,candidate)
            if value is None or score(candidate)<score(value):current,value=state,candidate
    iteration=-1;completed_iterations=0
    for iteration in range(max_iterations):
        if time.perf_counter()>=deadline:break
        completed_iterations+=1
        if current is None:
            # Randomized reconstruction can escape poor initial vehicle ordering.
            current=next(initial_states(problem))
            for layer in current.routes:rng.shuffle(current.routes[layer])
            value=evaluate(problem,current);evaluations+=1
            if value is None:current=None;continue
        used=[]
        if rng.random()<.65:
            d=rng.choices(DESTROY,weights=[weights[k] for k in DESTROY])[0]
            r=rng.choices(REPAIR,weights=[weights[k] for k in REPAIR])[0]
            candidate,removed=destroy(problem,current,value,d,rng)
            candidate=repair(problem,candidate,removed,r,rng,deadline);used=[d,r]
        else:candidate,_=mutate(problem,current,value,rng)
        for k in used:uses[k]+=1
        tested=evaluate(problem,candidate) if candidate is not None else None;evaluations+=1
        reward=0.
        if tested:
            if rng.random()<.2:
                candidate,tested=improve_pool(problem,candidate,tested,deadline,attempts=8)
            improvement=consider(candidate,tested)
            delta=score(tested)-score(value)
            progress=min(1.,(time.perf_counter()-begun)/max_time)
            temp=max(.001,max(1.,score(value)*.03)*(.01**progress))
            if delta<=0 or rng.random()<math.exp(-min(700.,delta/temp)):
                current,value=candidate,tested;accepted+=1;reward=2.
                if improvement:reward=8.
        for k in used:rewards[k]+=reward
        if (iteration+1)%40==0:
            for k in weights:
                if uses[k]:weights[k]=max(.1,.8*weights[k]+.2*rewards[k]/uses[k])
                uses[k]=0;rewards[k]=0.
        if (iteration+1)%150==0 and best_state is not None:
            current,value=best_state.copy(),best
    info=dict(method='alns',status='FEASIBLE' if best else 'NO_SOLUTION_FOUND',seed=seed,
              runtime=time.perf_counter()-begun,iterations=completed_iterations,evaluations=evaluations,accepted=accepted,
              operator_weights=weights,incumbent_history=history,objective_mode=problem.objective_mode,
              pool_max=problem.pool_max,proven_optimal=False,proven_infeasible=False,
              restricted_trip_domain=problem.restricted_trip_domain)
    result=best['result'] if best else dict(schema_version=3,model=10,policy_version=VERSION,
                                          batch_mode='no_batch',e1_trips=[],e2_trips=[],feasible=False)
    result.update(model_module='models.routing.model_10.alns',solver_module=__name__,info=info)
    if best:
        result['audit']=audit_solution(problem,result)
        if not result['audit']['valid']:raise RuntimeError('M10 ALNS incumbent failed final independent audit')
        info.update(objective=best['cost'] if problem.objective_mode=='transport' else best['pool'],transport_cost=best['cost'])
    return result


def iter_seeds(seeds,problem):
    yield from seeds
    yield from initial_states(problem)
