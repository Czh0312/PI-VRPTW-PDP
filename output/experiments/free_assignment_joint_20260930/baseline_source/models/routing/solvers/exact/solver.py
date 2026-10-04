"""Full-model solve and explicit schedule extraction, with honest proof metadata."""
from __future__ import annotations

import math
import time
from copy import deepcopy
from collections import defaultdict

from models.routing.common.cycle.state import empty_result
from .formulation import build_model
from .primal import construct_incumbent
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.precheck import check_necessary_conditions, rejected_result, travel_lower_bounds


def _set_start(problem, model, ctx, result):
    """Audit, relabel identical vehicles to satisfy symmetry, then load a start."""
    if result is None or not audit_solution(problem, result)['valid']:
        return False
    scheduled = {}
    for layer in ('e1', 'e2'):
        groups = defaultdict(list)
        for trip in result[layer+'_trips']:
            groups[trip.get('satellite_id', problem.depot)].append(trip)
        trips = []
        for rows in groups.values():
            vehicles = {v: i for i, v in enumerate(sorted({r['vehicle_id'] for r in rows}))}
            slots = defaultdict(int)
            for trip in sorted(rows, key=lambda r: (r['vehicle_id'], r.get('trip_index', 0))):
                copied = {**trip, 'vehicle_id': vehicles[trip['vehicle_id']]}
                if layer == 'e2':
                    copied['trip_index'] = slots[copied['vehicle_id']]
                    slots[copied['vehicle_id']] += 1
                trips.append(copied)
        for trip in trips:
            matches = [r for r in ctx[layer] if r['vehicle'] == trip['vehicle_id'] and
                       (r['batch'].id == trip['batch_id'] if layer == 'e1' else
                        r['satellite'] == trip['satellite_id'] and r['slot'] == trip['trip_index'])]
            if not matches:
                return False
            r = matches[0]
            order = []
            for event in trip['events']:
                index = next((i for i, n in enumerate(r['nodes']) if n['kind'] == event['kind'] and
                             (n['site'] == event['site_id'] if layer == 'e1' else
                              n['order'].id == event['order_id'])), None)
                if index is None:
                    return False
                order.append(index)
            arcs = set(zip([-1]+order, order+[-2]))
            if not arcs.issubset(r['x']):
                return False
            scheduled[r['label']] = trip, order, arcs
    # Set absent visits and arcs too; a partial start should not ask the solver
    # to rediscover that every covered task excludes all other route copies.
    for layer in ('e1', 'e2'):
        for r in ctx[layer]:
            entry = scheduled.get(r['label'])
            trip, order, arcs = entry if entry else (None, [], set())
            r['used'].Start = int(entry is not None)
            r['start'].Start = trip['departure'] if trip else 0
            r['end'].Start = trip['return_complete'] if trip else 0
            for arc, variable in r['x'].items():
                variable.Start = int(arc in arcs)
            for i, variable in r['visit'].items():
                variable.Start = int(i in order)
            for index, event in zip(order, trip['events'] if trip else []):
                r['times'][index].Start = event['start']
                quantity = r['nodes'][index]['quantity']
                if layer == 'e1' and event['kind'] == 'p' and not isinstance(quantity, int):
                    quantity.Start = event['quantity']
    return True


def solve_exact(problem, time_limit=60, seed=42, verbose=False, initial_result=None, _travel_bounds=None):
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    begun = time.perf_counter()
    travel_bounds = _travel_bounds or travel_lower_bounds(problem)
    precheck = check_necessary_conditions(problem, travel_bounds)
    if not precheck['passed']:
        return rejected_result(problem, 'exact', precheck, time.perf_counter()-begun)
    import gurobipy as gp
    from gurobipy import GRB
    result = empty_result(problem, 'exact')
    result['precheck'] = precheck
    supplied = initial_result if initial_result is not None and audit_solution(problem, initial_result)['valid'] else None
    generated = construct_incumbent(problem, min(begun+time_limit, time.perf_counter()+min(.05, time_limit*.05)))
    if generated is not None and (supplied is None or generated['info']['objective'] < supplied['info']['objective']):
        supplied = generated
    result['info']['primal_seed_found'] = generated is not None

    def preserve_incumbent():
        """A timeout or failed start completion must never discard a valid input."""
        info = result['info']
        if supplied is None or (result.get('audit', {}).get('valid') and
                info['objective'] <= supplied['info']['objective']+1e-9):
            return result
        kept = deepcopy(supplied)
        objective = supplied['info']['objective']
        bound = info.get('best_bound')
        conflict = info.get('proven_infeasible') or info.get('proven_optimal')
        if conflict:
            bound = None
        kept.update(solver='exact', precheck=precheck)
        kept['info'] = {**info, 'objective': objective, 'proven_optimal': False,
                        'proven_infeasible': False, 'incumbent_source': supplied['info'].get('incumbent_source', 'audited_start'),
                        'best_bound': bound, 'runtime': time.perf_counter()-begun,
                        'mip_gap': max(0, objective-bound)/max(1e-9, abs(objective)) if bound is not None else None}
        if conflict:
            kept['info']['status'] = 'MODEL_START_CONFLICT'
        return kept
    try:
        model, ctx = build_model(problem, verbose=verbose, deadline=begun+time_limit, travel_bounds=travel_bounds)
    except TimeoutError:
        result['info'].update(status='TIME_LIMIT_BUILD', runtime=time.perf_counter()-begun)
        return preserve_incumbent()
    try:
        result['info']['mip_start_supplied'] = _set_start(problem, model, ctx, supplied)
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
                    formulation_features=['sparse_time_arcs', 'vehicle_symmetry', 'causal_steps']+
                        (['fixed_B1_pickup_quantities'] if len(problem.operating_policy.batches) == 1 else [])+
                        (['shared_E2_task_states_and_internal_arcs'] if ctx['shared_e2'] else []),
                    shared_e2_arcs=ctx['shared_e2_arcs'],
                    e2_max_trips_per_vehicle=problem.operating_policy.e2_max_trips_per_vehicle)
        try:
            info['best_bound'] = float(model.ObjBound) if math.isfinite(model.ObjBound) else None
        except (AttributeError, gp.GurobiError):
            pass
        if not model.SolCount:
            info['proven_optimal'] = False
            return preserve_incumbent()
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
        return preserve_incumbent()
    finally:
        model.dispose()
