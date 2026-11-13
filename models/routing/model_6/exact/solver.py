"""One native MILP optimization over the entire declared M6 domain."""
import math
import time

from models.common.no_batch.audit import audit_solution
from models.common.no_batch.instance import VERSION


def solve_exact(problem, time_limit=60, seed=42, verbose=False, *, backend='highs',
                strategy='milp', milp_options=None):
    if problem.model_number != 6 or strategy not in ('milp','auto'):
        raise ValueError('M6 exact requires its own problem and direct MILP strategy')
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be positive and finite')
    options = dict(milp_options or {})
    if set(options)-{'threads'}:
        raise ValueError('M6 milp_options supports threads only')
    begun = time.perf_counter()
    from .formulation import build_model
    from .solution import extract_solution
    result = dict(schema_version=3, model=6, policy_version=VERSION, batch_mode='no_batch',
                  solver='exact', model_module='models.routing.model_6.exact', solver_module=__name__,
                  e1_trips=[], e2_trips=[], feasible=False)
    info = dict(method='exact', backend=backend, seed=seed, time_limit=time_limit,
                objective_mode=problem.objective_mode, pool_max=problem.pool_max,
                restricted_trip_domain=problem.restricted_trip_domain, proven_optimal=False,
                proven_infeasible=False)
    result['info'] = info
    try:
        m, ctx = build_model(problem, deadline=begun+time_limit)
        info.update(variables=len(m.variables), constraints=len(m.rows), build_seconds=time.perf_counter()-begun)
        remaining = time_limit-(time.perf_counter()-begun)
        if remaining <= 0:
            raise TimeoutError('MILP construction exhausted budget')
        raw = m.solve(backend, remaining, seed, options.get('threads',1), verbose)
        info.update(status=raw['status'], message=raw.get('message'))
        for key in ('bound','gap'):
            value = raw.get(key)
            info[key] = float(value) if value is not None and math.isfinite(value) else None
        info['proven_infeasible'] = raw['status'] == 'INFEASIBLE'
        if raw['x'] is not None:
            result.update(extract_solution(ctx, raw['x']))
            audit = audit_solution(problem, result)
            result['audit'] = audit
            result['feasible'] = audit['valid']
            native_objective=m.objective.value(raw['x'])
            physical_objective=problem.objective(audit['transport_cost'], audit['minimum_pool_for_schedule'])
            info['native_objective']=native_objective
            info['native_pool_certificate']=ctx['pool'].value(raw['x'])
            mismatch=(not math.isclose(native_objective,physical_objective,rel_tol=1e-6,abs_tol=1e-5)
                      if problem.objective_mode=='transport' or raw['status']=='OPTIMAL'
                      else physical_objective>native_objective+1e-5)
            if mismatch:
                audit['valid']=False
                audit['errors'].append(dict(code='MILP_OBJECTIVE_MISMATCH',native=native_objective,physical=physical_objective))
                result['feasible']=False
            elif physical_objective<native_objective-1e-5:
                info['postprocessed_pool_improvement']=native_objective-physical_objective
                info['native_gap']=info.get('gap')
                if info.get('bound') is not None:
                    info['gap']=max(0.,(physical_objective-info['bound'])/max(abs(physical_objective),1e-10))
            if audit['valid']:
                result['container_pool_size'] = audit['minimum_pool_for_schedule']
                info.update(transport_cost=audit['transport_cost'],
                            objective=physical_objective,
                            proven_optimal=raw['status']=='OPTIMAL')
            else:
                info['status'] = 'AUDIT_FAILED'
        elif raw['status'] == 'TIME_LIMIT':
            info['status'] = 'NO_SOLUTION_FOUND'
    except TimeoutError as exc:
        info.update(status='NO_SOLUTION_FOUND', message=str(exc))
    info['runtime'] = time.perf_counter()-begun
    return result
