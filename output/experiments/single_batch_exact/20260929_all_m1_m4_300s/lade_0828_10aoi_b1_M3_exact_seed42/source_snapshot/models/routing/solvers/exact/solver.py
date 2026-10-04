"""Full-model solve and explicit schedule extraction, with honest proof metadata."""
from __future__ import annotations

import math
import time

from models.routing.common.cycle.state import empty_result
from .formulation import build_model
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.precheck import check_necessary_conditions, rejected_result


def solve_exact(problem, time_limit=60, seed=42, verbose=False):
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    begun = time.perf_counter()
    precheck = check_necessary_conditions(problem)
    if not precheck['passed']:
        return rejected_result(problem, 'exact', precheck, time.perf_counter()-begun)
    import gurobipy as gp
    from gurobipy import GRB
    result = empty_result(problem, 'exact')
    result['precheck'] = precheck
    try:
        model, ctx = build_model(problem, verbose=verbose, deadline=begun+time_limit)
    except TimeoutError:
        result['info'].update(status='TIME_LIMIT_BUILD', runtime=time.perf_counter()-begun)
        return result
    try:
        build_time = time.perf_counter()-begun
        model.Params.TimeLimit = max(0, time_limit-build_time)
        model.Params.Seed = int(seed)
        model.optimize()
        status = {GRB.OPTIMAL: 'OPTIMAL', GRB.INFEASIBLE: 'INFEASIBLE', GRB.TIME_LIMIT: 'TIME_LIMIT',
                  GRB.INTERRUPTED: 'INTERRUPTED', GRB.INF_OR_UNBD: 'INF_OR_UNBD'}.get(model.Status, str(model.Status))
        info = result['info']
        info.update(status=status, proven_optimal=model.Status == GRB.OPTIMAL,
                    proven_infeasible=model.Status == GRB.INFEASIBLE,
                    proof_scope='full_integer_time_multibatch_indexed',
                    build_time=build_time, mip_runtime=model.Runtime,
                    runtime=time.perf_counter()-begun, sol_count=model.SolCount,
                    variables=model.NumVars, constraints=model.NumConstrs+model.NumGenConstrs,
                    time_tick_seconds=problem.operating_policy.time_tick_seconds,
                    e2_max_trips_per_vehicle=problem.operating_policy.e2_max_trips_per_vehicle)
        try:
            info['best_bound'] = float(model.ObjBound) if math.isfinite(model.ObjBound) else None
        except (AttributeError, gp.GurobiError):
            pass
        if not model.SolCount:
            info['proven_optimal'] = False
            return result
        info.update(objective=model.ObjVal, mip_gap=model.MIPGap)

        def int_value(var):
            raw = var if isinstance(var, int) else var.X
            if abs(raw-round(raw)) > 1e-4:
                raise RuntimeError(f'Nonintegral schedule/quantity in incumbent: {raw}')
            return int(round(raw))

        for layer in ('e1', 'e2'):
            for r in ctx[layer]:
                if r['used'].X < 0.5:
                    continue
                following = {a: b for (a, b), x in r['x'].items() if x.X > 0.5}
                order, cursor = [], -1
                while following.get(cursor, -2) != -2:
                    cursor = following[cursor]
                    if cursor in order:
                        raise RuntimeError('Cycle in extracted route')
                    order.append(cursor)
                expected = {i for i, v in r['visit'].items() if v.X > 0.5}
                if set(order) != expected or len(order) != len(expected):
                    raise RuntimeError('Disconnected incumbent route')
                trip = dict(trip_id=r['label'], vehicle_id=r['vehicle'],
                            departure=int_value(r['start']), return_complete=int_value(r['end']), events=[])
                if layer == 'e1':
                    trip['batch_id'] = r['batch'].id
                else:
                    trip.update(satellite_id=r['satellite'], trip_index=r['slot'])
                for i in order:
                    n = r['nodes'][i]
                    event = dict(kind=n['kind'], start=int_value(r['times'][i]), quantity=int_value(n['quantity']))
                    event['site_id' if layer == 'e1' else 'order_id'] = n['site'] if layer == 'e1' else n['order'].id
                    trip['events'].append(event)
                result[layer+'_trips'].append(trip)
        audit = audit_solution(problem, result)
        result.update(audit=audit, cutoff_snapshots=audit.get('cutoff_snapshots', []),
                      depot_releases=audit.get('depot_releases', []))
        if not audit['valid']:
            info.update(status='AUDIT_FAILED', proven_optimal=False)
        return result
    finally:
        model.dispose()
