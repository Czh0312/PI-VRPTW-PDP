"""Solve and extract the full joint MILP; no ALNS imports or starts."""
from __future__ import annotations

import math
import time

from ..model import validate_problem
from .model import build_model
from models.routing.common.cycle.joint import joint_empty_result, lift_result
from models.routing.common.cycle.state import empty_result


def solve_exact(problem, time_limit=60, seed=42, verbose=False):
    validate_problem(problem)
    if isinstance(time_limit,bool) or not math.isfinite(time_limit) or time_limit<=0:
        raise ValueError('time_limit must be finite and positive')
    begun=time.perf_counter()
    result=joint_empty_result(problem,'exact')
    missing=[oid for oid,batches in problem.legal_batches.items() if not batches]
    if missing:
        result['info'].update(status='INFEASIBLE',proven_infeasible=True,
                              proof_scope='empty_necessary_order_batch_domain',orders=missing,
                              runtime=time.perf_counter()-begun)
        return result
    try:
        m,ctx=build_model(problem,verbose=verbose,deadline=begun+time_limit)
    except TimeoutError:
        result['info'].update(status='TIME_LIMIT_BUILD',runtime=time.perf_counter()-begun)
        return result
    try:
        import gurobipy as gp
        from gurobipy import GRB
        m.Params.TimeLimit=max(0,time_limit-(time.perf_counter()-begun))
        m.Params.Seed=int(seed)
        m.optimize()
        status={GRB.OPTIMAL:'OPTIMAL',GRB.INFEASIBLE:'INFEASIBLE',GRB.TIME_LIMIT:'TIME_LIMIT',
                GRB.INTERRUPTED:'INTERRUPTED',GRB.INF_OR_UNBD:'INF_OR_UNBD'}.get(m.Status,str(m.Status))
        info=dict(status=status,proven_optimal=m.Status==GRB.OPTIMAL,proven_infeasible=m.Status==GRB.INFEASIBLE,
                  objective=None,best_bound=float(m.ObjBound) if math.isfinite(m.ObjBound) else None,
                  mip_gap=None,runtime=time.perf_counter()-begun,build_time=time.perf_counter()-begun-m.Runtime,
                  variables=m.NumVars,constraints=m.NumConstrs+m.NumGenConstrs,node_count=m.NodeCount,
                  sol_count=m.SolCount,proof_scope='full_joint_batch_pool_cost_integer_time_MILP',
                  candidate_slots=len(problem.operating_policy.batches),
                  time_tick_seconds=problem.operating_policy.time_tick_seconds,
                  e2_max_trips_per_vehicle=problem.operating_policy.e2_max_trips_per_vehicle)
        result['info']=info
        if not m.SolCount:
            info['proven_optimal']=False
            return result

        def integral(v):
            value=v if isinstance(v,int) else v.getValue() if isinstance(v,gp.LinExpr) else v.X
            if abs(value-round(value))>1e-4:
                raise RuntimeError(f'Nonintegral M6 schedule or quantity: {value}')
            return int(round(value))

        assignment={oid:bid for (oid,bid),v in ctx['assignments'].items() if v.X>.5}
        pool=integral(ctx['pool'])
        fixed=problem.project(assignment,pool)
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
        result=lift_result(problem,raw,assignment,pool,'exact')
        result['solver_module']=__name__
        info=result['info']
        if result.get('audit',{}).get('valid'):
            if not math.isclose(info['objective'],m.ObjVal,abs_tol=1e-5,rel_tol=1e-7):
                info.update(status='MODEL_COST_MISMATCH',proven_optimal=False)
                raise RuntimeError('Joint MILP objective disagrees with independent cost replay')
            info.update(mip_gap=max(0,info['objective']-info['best_bound'])/max(1e-9,abs(info['objective'])),
                        milp_objective=float(m.ObjVal),runtime=time.perf_counter()-begun)
        else:
            info.update(status='AUDIT_FAILED',proven_optimal=False)
        return result
    finally:
        m.dispose()
