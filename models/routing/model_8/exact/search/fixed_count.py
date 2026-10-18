"""Solve and extract the full joint MILP with audited physical MIP starts."""
from __future__ import annotations

import math
import time

from models.common.cycle.scope import validate_problem
from models.routing.model_8.exact.formulations.fixed_count import build_model
from models.common.cycle.joint import joint_empty_result, lift_result
from models.common.cycle.state import empty_result
from models.routing.model_8.exact.initialization import audited_start, install_start, retain_incumbent
from models.routing.model_8.exact.initialization import install_assignment_guidance


def solve_exact(problem, time_limit=60, seed=42, verbose=False, initial_result=None, budget_strategy='adaptive'):
    validate_problem(problem, 6)
    if isinstance(time_limit,bool) or not math.isfinite(time_limit) or time_limit<=0:
        raise ValueError('time_limit must be finite and positive')
    begun=time.perf_counter()
    incumbent=audited_start(problem, initial_result)
    result=joint_empty_result(problem,'exact')
    from models.common.cycle.pool_bound import necessary_cost_lower_bound
    analytical_bound=necessary_cost_lower_bound(problem)
    result['info']['best_bound']=analytical_bound
    missing=[oid for oid,batches in problem.legal_batches.items() if not batches]
    if missing:
        result['info'].update(status='INFEASIBLE',proven_infeasible=True,
                              proof_scope='empty_necessary_order_batch_domain',orders=missing,
                              runtime=time.perf_counter()-begun)
        return result
    if len(problem.batch_map) > len(problem.orders):
        result['info'].update(status='INFEASIBLE', proven_infeasible=True,
                              proof_scope='nonempty_batch_assignment_impossible',
                              runtime=time.perf_counter()-begun)
        return result
    try:
        m,ctx=build_model(problem,verbose=verbose,deadline=begun+time_limit)
    except TimeoutError:
        result['info'].update(status='TIME_LIMIT_BUILD',runtime=time.perf_counter()-begun)
        return retain_incumbent(result, incumbent, problem, time.perf_counter()-begun)
    try:
        import gurobipy as gp
        from gurobipy import GRB
        start_variables, hint_variables = ((install_start(m, ctx, incumbent), 0)
            if incumbent is not None else install_assignment_guidance(problem, m, ctx))
        m.Params.TimeLimit=max(0,time_limit-(time.perf_counter()-begun))
        m.Params.Seed=int(seed)
        m.Params.MIPFocus=2 if incumbent is not None else 1
        m.optimize()
        status={GRB.OPTIMAL:'OPTIMAL',GRB.INFEASIBLE:'INFEASIBLE',GRB.TIME_LIMIT:'TIME_LIMIT',
                GRB.INTERRUPTED:'INTERRUPTED',GRB.INF_OR_UNBD:'INF_OR_UNBD'}.get(m.Status,str(m.Status))
        info=dict(status=status,proven_optimal=m.Status==GRB.OPTIMAL,proven_infeasible=m.Status==GRB.INFEASIBLE,
                  objective=None,best_bound=max(analytical_bound,float(m.ObjBound)) if math.isfinite(m.ObjBound) else analytical_bound,
                  mip_gap=None,runtime=time.perf_counter()-begun,build_time=time.perf_counter()-begun-m.Runtime,
                  variables=m.NumVars,constraints=m.NumConstrs+m.NumGenConstrs,node_count=m.NodeCount,
                  sol_count=m.SolCount,proof_scope=('full_joint_batch_pool_shared_cutoff_cost_integer_time_MILP'
                      if problem.shared_cutoff else 'full_joint_batch_pool_cutoff_cost_integer_time_MILP'
                      if problem.optimize_cutoffs else 'full_joint_batch_pool_cost_integer_time_MILP'),
                  candidate_slots=len(problem.operating_policy.batches),
                  time_tick_seconds=problem.operating_policy.time_tick_seconds,
                  time_tick_subdivision=problem.operating_policy.time_tick_subdivision,
                  e2_max_trips_per_vehicle=problem.operating_policy.e2_max_trips_per_vehicle)
        info.update(compact_e2=ctx["parameters"].compact_e2, binary_variables=m.NumBinVars,
                    mip_start_variables=start_variables, assignment_hint_variables=hint_variables,
                    mip_start_supplied=incumbent is not None)
        result['info']=info
        if not m.SolCount:
            info['proven_optimal']=False
            return retain_incumbent(result, incumbent, problem, time.perf_counter()-begun)

        def integral(v):
            value=v if isinstance(v,int) else v.getValue() if isinstance(v,gp.LinExpr) else v.X
            if abs(value-round(value))>1e-4:
                raise RuntimeError(f'Nonintegral M6 schedule or quantity: {value}')
            return int(round(value))

        assignment={oid:bid for (oid,bid),v in ctx['assignments'].items() if v.X>.5}
        pool=integral(ctx['pool'])
        cutoffs={bid:integral(value) for bid,value in ctx['cutoffs'].items()}
        selected_problem = problem
        if getattr(problem, 'variable_calendar', False):
            boundaries = [integral(ctx['batch_starts'][b.id]) for b in problem.operating_policy.batches]
            boundaries.append(problem.operating_policy.cycle_end)
            selected_problem = problem.with_boundaries(boundaries)
            info['proof_scope'] = 'variable_boundaries_independent_cutoffs_joint_cost_integer_time_MILP'
        fixed=selected_problem.project(assignment,pool,cutoffs)
        raw=empty_result(fixed,'exact')
        raw['info']={**info,'objective':None}
        for layer in ('e1','e2'):
            for r in ctx[layer]:
                if r['used'].X<.5:
                    continue
                following={a:b for (a,b),v in r['x'].items() if v.X>.5}
                order,cursor=[],-1
                while following.get(cursor,-2)!=-2:
                    cursor=following[cursor]
                    if cursor in order:
                        raise RuntimeError('Cycle in M6 incumbent')
                    order.append(cursor)
                if set(order)!={i for i,v in r['visit'].items() if v.X>.5}:
                    raise RuntimeError('Disconnected M6 incumbent')
                trip=dict(trip_id=r['label'],vehicle_id=r['vehicle'],departure=integral(r['start']),
                          return_complete=integral(r['end']),events=[])
                if layer=='e1':
                    trip['batch_id']=r['batch'].id
                else:
                    trip.update(satellite_id=r['satellite'],trip_index=r['slot'])
                for i in order:
                    n=r['nodes'][i]
                    event=dict(kind=n['kind'],start=integral(r['times'][i]),quantity=integral(n['quantity']))
                    event['site_id' if layer=='e1' else 'order_id']=n['site'] if layer=='e1' else n['order'].id
                    trip['events'].append(event)
                raw[layer+'_trips'].append(trip)
        result=lift_result(selected_problem,raw,assignment,pool,'exact',cutoffs)
        result['solver_module']=__name__
        info=result['info']
        if result.get('audit',{}).get('valid'):
            if not math.isclose(info['objective'],m.ObjVal,abs_tol=1e-5,rel_tol=1e-7):
                info.update(status='MODEL_COST_MISMATCH',proven_optimal=False)
                raise RuntimeError('Joint MILP objective disagrees with independent cost replay')
            info.update(mip_gap=max(0,info['objective']-info['best_bound'])/max(1e-9,abs(info['objective'])),
                        milp_objective=float(m.ObjVal),runtime=time.perf_counter()-begun)
            info['optimized_cutoffs']=problem.optimize_cutoffs
        else:
            info.update(status='AUDIT_FAILED',proven_optimal=False)
        return retain_incumbent(result, incumbent, problem, time.perf_counter()-begun)
    finally:
        m.dispose()
