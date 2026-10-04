"""Optimize E1 with audited, fixed E2 sequences. Feasible upper bounds only.

Fixed times or optional joint E2 retiming determine actual cutoff quantities
and actual supply deadlines; order-level virtual returns are not sufficient.
Use complete E1 route enumeration only for <=4 active sites and zero E1
turnaround. Otherwise the indexed E1 model retains physical vehicle sequences.
The subproblem bound/optimality never certifies full M5 optimality.
"""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import time

from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.state import empty_result
from .multibatch_e1_inventory import build_e1_inventory, set_e1_projection
from .multibatch_e1_routes import build_e1_routes, set_route_projection


def fix_e2_schedule(problem, context, initial):
    """Actual E2 timings, not virtual independent order timings, constrain E1."""
    returned = {e['order_id']: trip['return_complete'] for trip in initial['e2_trips']
                for e in trip['events'] if e['kind'] == 'p'}
    for oid, value in returned.items():
        context['returns'][oid].LB = context['returns'][oid].UB = value
        first = next(b.id for b in problem.operating_policy.batches if value <= b.cutoff)
        for b in problem.operating_policy.batches:
            var = context['allocation'][oid, b.id]
            var.LB = var.UB = int(first == b.id)
    supplies = context.get('supplies', context.get('deliveries'))
    for trip in initial['e2_trips']:
        for event in trip['events']:
            if event['kind'] == 'd':
                o = problem.order_map[event['order_id']]
                latest = trip['departure']-problem.operating_policy.e2_dispatch_prep_time
                supplies[o.satellite, o.batch].UB = min(supplies[o.satellite, o.batch].UB, latest)


def add_e2_timing(problem, model, context, initial):
    """Keep every actual E2 arc and physical vehicle, optimize only its times."""
    from gurobipy import GRB
    p, supplies = problem.operating_policy, context.get('supplies', context.get('deliveries'))
    timings, vehicles = {}, {}
    for index, trip in enumerate(initial['e2_trips']):
        start = model.addVar(vtype=GRB.INTEGER, lb=p.cycle_start, ub=p.cycle_end, name=f'e2_departure_{index}')
        end = model.addVar(vtype=GRB.INTEGER, lb=p.cycle_start, ub=p.cycle_end, name=f'e2_end_{index}')
        start.Start, end.Start = trip['departure'], trip['return_complete']
        deliveries = [e for e in trip['events'] if e['kind'] == 'd']
        prep = p.e2_dispatch_prep_time if deliveries else 0
        model.addConstr(start-prep >= p.cycle_start)
        model.addConstr(end+p.e2_turnaround_time <= p.cycle_end)
        if any(e['kind'] == 'p' for e in trip['events']):
            model.addConstr(end <= p.batches[-1].cutoff)
        previous, clock = trip['satellite_id'], start
        for event in trip['events']:
            order = problem.order_map[event['order_id']]
            at = context['order_times'][order.id, event['kind']]
            at.Start = event['start']
            model.addConstr(at >= clock+problem.travel(previous, order.customer))
            if event['kind'] == 'd':
                model.addConstr(start >= supplies[order.satellite, order.batch]+prep)
            else:
                model.addConstr(context['returns'][order.id] == end)
            previous, clock = order.customer, at+order.service_time
        model.addConstr(end >= clock+problem.travel(previous, trip['satellite_id'])+p.e2_return_unload_time)
        key = trip['satellite_id'], trip['vehicle_id']
        vehicles.setdefault(key, []).append((trip['trip_index'], start, end, prep))
        timings[index] = start, end
    for rows in vehicles.values():
        rows.sort(key=lambda row: row[0])
        for before, after in zip(rows, rows[1:]):
            model.addConstr(after[1]-after[3] >= before[2]+p.e2_turnaround_time)
    context['e2_timing'] = timings


def extract_routes(problem, context, backend, e2_trips):
    p = problem.operating_policy
    result = empty_result(problem, 'exact_E1_fixed_E2_polish')
    result['e2_trips'] = deepcopy(e2_trips)
    if 'e2_timing' in context:
        for index, trip in enumerate(result['e2_trips']):
            start, end = context['e2_timing'][index]
            trip['departure'], trip['return_complete'] = int(round(start.X)), int(round(end.X))
            for event in trip['events']:
                event['start'] = int(round(context['order_times'][event['order_id'], event['kind']].X))
    ids = {t['trip_id'] for t in e2_trips}
    def append(bid, vehicle, departure, returned, events):
        rid = f'polish_e1_{bid}_{vehicle}'
        while rid in ids:
            rid += '_'
        ids.add(rid)
        result['e1_trips'].append(dict(trip_id=rid, batch_id=bid, vehicle_id=vehicle,
            departure=int(round(departure)), return_complete=int(round(returned)), events=events))
    if backend == 'patterns':
        next_vehicle = {b.id: 0 for b in p.batches}
        for i, pattern in enumerate(context['patterns']):
            if context['chosen'][i].X < .5:
                continue
            bid = pattern['batch']
            current, previous, events = pattern['departure'], problem.depot, []
            for site in pattern['delivery']:
                current += problem.travel(previous, site)
                events.append(dict(kind='d', site_id=site, start=int(current), quantity=problem.demands[site, bid]))
                current += problem.service(site)
                previous = site
            if pattern['pickup']:
                current = max(current, problem.batch_map[bid].cutoff)
            for site in pattern['pickup']:
                current += problem.travel(previous, site)
                events.append(dict(kind='p', site_id=site, start=int(current),
                    quantity=int(round(context['quantities'][i, site].X))))
                current += problem.service(site)
                previous = site
            append(bid, next_vehicle[bid], pattern['departure'], pattern['return_complete'], events)
            next_vehicle[bid] += 1
    else:
        for r in context['routes']:
            if r['used'].X < .5:
                continue
            edges = {a: b for (a, b), v in r['x'].items() if v.X > .5}
            node, seen, events = edges.get(-1), set(), []
            while node is not None and node != -2 and node not in seen:
                seen.add(node)
                task = r['nodes'][node]
                amount = task['quantity'] if task['kind'] == 'd' else task['quantity'].X
                events.append(dict(kind=task['kind'], site_id=task['site'],
                    quantity=int(round(amount)), start=int(round(r['times'][node].X))))
                node = edges.get(node)
            if node != -2 or len(seen) != sum(v.X > .5 for v in r['visit'].values()):
                raise RuntimeError('E1 polish extraction is not a single elementary route')
            append(r['batch'].id, r['vehicle'], r['start'].X, r['end'].X, events)
    return result


def reoptimize_e1(problem, initial_result, *, time_limit=5, seed=42, verbose=False,
                  backend='auto', diagnostics_path=None, retime_e2=False):
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    if (problem.model_number != 5 or problem.free_assignment or not problem.reusable
            or len(problem.operating_policy.batches) <= 1):
        raise ValueError('E1 polishing requires fixed reusable multibatch M5')
    if backend not in ('auto', 'patterns', 'indexed'):
        raise ValueError('backend must be auto, patterns or indexed')
    p = problem.operating_policy
    pattern_scope = len({o.satellite for o in problem.orders}) <= 4 and p.e1_turnaround_time == 0
    selected = ('patterns' if pattern_scope else 'indexed') if backend == 'auto' else backend
    if selected == 'patterns' and not pattern_scope:
        raise ValueError('Feasible pattern polishing requires <=4 active sites and zero E1 turnaround')
    begun, model = time.perf_counter(), None
    deadline = begun+time_limit
    original_audit = audit_solution(problem, initial_result)
    if not original_audit['valid']:
        raise ValueError('Initial result must pass the full M5 independent audit')
    result = deepcopy(initial_result)
    # Derived state is rebuilt after route changes; subproblem optimality
    # is separate from all complete-M5 proof flags and bounds.
    for field in ('cutoff_snapshots', 'depot_releases', 'audit'):
        result.pop(field, None)
    result['info'] = dict(status='FEASIBLE', proven_optimal=False, proven_infeasible=False,
        objective=original_audit['objective'], best_bound=None, mip_gap=None,
        proof_scope='audited_fixed_E2_sequences_E1_polish' if retime_e2 else 'audited_fixed_E2_E1_polish',
        upper_before=original_audit['objective'], e2_retiming_enabled=bool(retime_e2),
        e1_before=original_audit['distances']['e1'], e1_polish_backend=selected,
        e1_subproblem_optimal=False, e1_subproblem_lower_bound=None)
    status, candidate_audit = 'TIME_LIMIT_BUILD', None
    try:
        import gurobipy as gp
        from gurobipy import GRB
        if selected == 'patterns':
            model, context = build_e1_routes(problem, deadline=deadline, inventory=True, verbose=verbose)
            set_route_projection(problem, context, initial_result)
        else:
            model, context = build_e1_inventory(problem, deadline=deadline, inventory=True, verbose=verbose)
            set_e1_projection(problem, context, initial_result)
        if retime_e2:
            add_e2_timing(problem, model, context, initial_result)
        else:
            fix_e2_schedule(problem, context, initial_result)
        model.Params.Seed = int(seed)
        model.Params.TimeLimit = max(0, deadline-time.perf_counter())
        model.optimize()
        status = int(model.Status)
        if diagnostics_path is not None:
            path = Path(diagnostics_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            model.write(str(path))
        try:
            raw = model.ObjBound
        except (gp.GurobiError, AttributeError):
            raw = None
        result['info'].update(e1_subproblem_optimal=model.Status == GRB.OPTIMAL,
            e1_subproblem_lower_bound=float(raw) if raw is not None and math.isfinite(raw) else None,
            e1_polish_variables=model.NumVars, e1_polish_work=model.Work)
        if model.SolCount:
            candidate = extract_routes(problem, context, selected, initial_result['e2_trips'])
            candidate_audit = audit_solution(problem, candidate)
            if not candidate_audit['valid']:
                raise RuntimeError(f'E1 polishing failed full cycle audit: {candidate_audit["errors"]}')
            if not math.isclose(candidate_audit['distances']['e1'], model.ObjVal, rel_tol=1e-7, abs_tol=1e-5):
                raise RuntimeError('E1 polishing objective differs from independently replayed routes')
            if candidate_audit['objective'] < original_audit['objective']-1e-6:
                candidate['info'] = result['info']
                result = candidate
    except TimeoutError:
        pass
    finally:
        if model is not None:
            model.dispose()
    audited = audit_solution(problem, {**result, 'info': {**result['info'], 'objective': None}})
    if not audited['valid']:
        raise RuntimeError('Returned E1 polishing schedule is invalid')
    result['info'].update(objective=audited['objective'], e1_after=audited['distances']['e1'],
        e1_polish_improved=audited['objective'] < original_audit['objective']-1e-6,
        e2_times_changed=result['e2_trips'] != initial_result['e2_trips'],
        e1_polish_status=status, runtime=time.perf_counter()-begun)
    result['audit'] = audited
    return result
