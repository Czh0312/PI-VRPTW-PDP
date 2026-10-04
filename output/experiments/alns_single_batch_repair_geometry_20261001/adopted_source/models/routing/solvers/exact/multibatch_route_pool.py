"""Audited M5 upper bounds from a restricted E2 route-pool integer master.

Candidate generation is bounded and incomplete. The master retains actual
physical vehicles, task times, E1 supply, common trip returns, cutoff stock
and cleaning. Its bound and optimality apply ONLY to the candidate pool.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from itertools import permutations
import math
from pathlib import Path
import time

from models.routing.common.cycle.audit import audit_solution
from .multibatch_e1_inventory import build_e1_inventory, set_e1_projection
from .multibatch_e1_routes import build_e1_routes, set_route_projection
from .multibatch_e1_polish import extract_routes
from .multibatch_route_pricing import evaluate_column, route_context


def candidate_pools(problem, initial, *, deadline=None, max_columns=128, transfers=True):
    """Keep original routes, reorder tasks, and transfer tasks/whole orders.

    Each domain belongs to one existing physical trip slot. Empty slots are
    allowed by the master, but no new slots are introduced. The original
    sequence is always retained. Pruning is heuristic, never a proof.
    """
    def expired():
        return deadline is not None and time.perf_counter() >= deadline
    contexts = {s: route_context(problem, s) for s in sorted({t['satellite_id'] for t in initial['e2_trips']})}
    trips = initial['e2_trips']
    originals = [tuple((e['order_id'], e['kind']) for e in t['events']) for t in trips]
    pools = [dict() for _ in trips]
    cache = {}
    def evaluate(index, sequence):
        sequence = tuple(sequence)
        key = trips[index]['satellite_id'], sequence
        if key not in cache:
            cache[key] = evaluate_column(problem, contexts[key[0]], sequence)
        return cache[key]
    def add(index, sequence):
        column = evaluate(index, sequence)
        if column is not None:
            pools[index][column['tasks']] = column
        return column
    for i, sequence in enumerate(originals):
        if add(i, sequence) is None:
            raise RuntimeError('Audited original trip did not project to the route pool')
    for i, sequence in enumerate(originals):
        if expired():
            break
        if len(sequence) <= 6:
            proposals = permutations(sequence)
        else:
            proposals = (sequence[:a]+sequence[a+1:b]+(sequence[a],)+sequence[b:]
                         if a < b else sequence[:b]+(sequence[a],)+sequence[b:a]+sequence[a+1:]
                         for a in range(len(sequence)) for b in range(len(sequence)+1) if a != b)
        for proposed in proposals:
            if expired():
                break
            add(i, proposed)
    if transfers:
        for i, source in enumerate(originals):
            if expired():
                break
            units = [(key,) for key in source]
            units += [tuple(key for key in source if key[0] == oid)
                      for oid in sorted({key[0] for key in source})
                      if sum(key[0] == oid for key in source) == 2]
            for unit in units:
                if expired():
                    break
                rest = tuple(key for key in source if key not in unit)
                if rest and evaluate(i, rest) is None:
                    continue
                for j, target in enumerate(originals):
                    if i == j or trips[i]['satellite_id'] != trips[j]['satellite_id']:
                        continue
                    if expired():
                        break
                    for a in range(len(target)+1):
                        one = target[:a]+(unit[0],)+target[a:]
                        proposals = [one] if len(unit) == 1 else (
                            one[:b]+(unit[1],)+one[b:] for b in range(a+1, len(one)+1))
                        for proposed in proposals:
                            if expired():
                                break
                            if add(j, proposed) is not None and rest:
                                add(i, rest)
    output = []
    for original, pool in zip(originals, pools):
        # Preserve both a cheap path and an early-return path for each task
        # subset. Neither criterion dominates actual joint scheduling.
        subsets = defaultdict(list)
        for column in pool.values():
            subsets[frozenset(column['tasks'])].append(column)
        kept = {original: pool[original]}
        for rows in subsets.values():
            for key in (lambda c: (c['cost'], c['return_complete'], c['tasks']),
                        lambda c: (c['return_complete'], c['cost'], c['tasks'])):
                column = min(rows, key=key)
                kept[column['tasks']] = column
        ranked = sorted((c for key, c in kept.items() if key != original),
                        key=lambda c: (c['cost']/len(c['tasks']), c['cost'], c['tasks']))
        output.append([pool[original]]+ranked[:max_columns-1])
    return output


def add_route_pool(problem, model, context, initial, pools, *, deadline=None):
    """Factor shared task arcs while retaining physical route endpoints."""
    import gurobipy as gp
    from gurobipy import GRB
    p, trips = problem.operating_policy, initial['e2_trips']
    limit = max(1, 2*len(problem.orders)+2*len(trips))
    ranks = {key: model.addVar(ub=limit, name=f'pool_task_rank_{key[0]}_{key[1]}')
             for key in context['order_times']}
    for o in problem.orders:
        model.addConstr(ranks[o.id, 'p'] >= ranks[o.id, 'd']+1)
    cover, internal, slots, choices = defaultdict(list), defaultdict(list), [], []
    supplies = context.get('supplies', context.get('deliveries'))
    def check():
        if deadline is not None and time.perf_counter() >= deadline:
            raise TimeoutError('Route-pool master construction reached its total budget')
    for i, (trip, columns) in enumerate(zip(trips, pools)):
        check()
        z = model.addVars(len(columns), vtype=GRB.BINARY,
            obj={j: c['cost'] for j, c in enumerate(columns)}, name=f'pool_choice_{i}')
        used = model.addVar(vtype=GRB.BINARY, name=f'pool_used_{i}')
        start = model.addVar(vtype=GRB.INTEGER, ub=p.cycle_end, name=f'pool_start_{i}')
        end = model.addVar(vtype=GRB.INTEGER, ub=p.cycle_end, name=f'pool_end_{i}')
        first_rank = model.addVar(ub=limit, name=f'pool_start_rank_{i}')
        last_rank = model.addVar(ub=limit, name=f'pool_end_rank_{i}')
        model.addConstr(gp.quicksum(z.values()) == used)
        delivery = gp.quicksum(z[j] for j, c in enumerate(columns) if any(k == 'd' for _, k in c['tasks']))
        prep = p.e2_dispatch_prep_time*delivery
        model.addConstr(start >= p.cycle_start*used+prep)
        model.addConstr(start <= p.cycle_end*used)
        model.addConstr(end <= (p.cycle_end-p.e2_turnaround_time)*used)
        model.addConstr(first_rank >= used)
        model.addConstr(first_rank <= limit*used)
        model.addConstr(last_rank <= limit*used)
        first, last, pickups, batches = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
        for j, c in enumerate(columns):
            if j % 64 == 0:
                check()
            z[j].Start = int(j == 0)
            first[c['tasks'][0]].append(z[j])
            last[c['tasks'][-1]].append(z[j])
            bids = set()
            for key in c['tasks']:
                cover[key].append(z[j])
                if key[1] == 'p':
                    pickups[key[0]].append(z[j])
                else:
                    bids.add(problem.order_map[key[0]].batch)
            assert len(bids) <= 1
            for bid in bids:
                batches[bid].append(z[j])
            for a, b in zip(c['tasks'], c['tasks'][1:]):
                internal[a, b].append(z[j])
        def active(rows, name):
            v = model.addVar(vtype=GRB.BINARY, name=name)
            model.addConstr(v == gp.quicksum(rows))
            return v
        for key, rows in first.items():
            v = active(rows, f'pool_first_{i}_{key[0]}_{key[1]}')
            model.addConstr((v == 1) >> (context['order_times'][key] >= start+problem.travel(trip['satellite_id'], problem.order_map[key[0]].customer)))
            model.addConstr((v == 1) >> (ranks[key] >= first_rank+1))
        for key, rows in last.items():
            v = active(rows, f'pool_last_{i}_{key[0]}_{key[1]}')
            o = problem.order_map[key[0]]
            model.addConstr((v == 1) >> (end >= context['order_times'][key]+o.service_time+problem.travel(o.customer, trip['satellite_id'])+p.e2_return_unload_time))
            model.addConstr((v == 1) >> (last_rank >= ranks[key]+1))
        for oid, rows in pickups.items():
            v = active(rows, f'pool_pickup_{i}_{oid}')
            model.addConstr((v == 1) >> (context['returns'][oid] == end))
        for bid, rows in batches.items():
            v = active(rows, f'pool_batch_{i}_{bid}')
            model.addConstr((v == 1) >> (start >= supplies[trip['satellite_id'], bid]+p.e2_dispatch_prep_time))
        used.Start, start.Start, end.Start = 1, trip['departure'], trip['return_complete']
        for event in trip['events']:
            context['order_times'][event['order_id'], event['kind']].Start = event['start']
        slots.append(dict(used=used, start=start, end=end, prep=prep, first_rank=first_rank, last_rank=last_rank))
        choices.append(z)
    for key in context['order_times']:
        model.addConstr(gp.quicksum(cover[key]) == 1, f'pool_cover_{key[0]}_{key[1]}')
    for n, ((a, b), rows) in enumerate(internal.items()):
        check()
        v = model.addVar(vtype=GRB.BINARY, name=f'pool_link_{n}')
        model.addConstr(v == gp.quicksum(rows))
        left, right = problem.order_map[a[0]], problem.order_map[b[0]]
        model.addConstr((v == 1) >> (context['order_times'][b] >= context['order_times'][a]+left.service_time+problem.travel(left.customer, right.customer)))
        model.addConstr((v == 1) >> (ranks[b] >= ranks[a]+1))
    vehicles = defaultdict(list)
    for i, trip in enumerate(trips):
        vehicles[trip['satellite_id'], trip['vehicle_id']].append(i)
    for indices in vehicles.values():
        indices.sort(key=lambda i: trips[i]['trip_index'])
        # Inactive intermediate slots must not break a physical vehicle's
        # timeline. Link every pair of active slots, including across holes.
        for pos, i in enumerate(indices):
            for j in indices[pos+1:]:
                a, b = slots[i], slots[j]
                inactive = 2-a['used']-b['used']
                model.addConstr(b['start']-b['prep'] >= a['end']+p.e2_turnaround_time-(p.cycle_end+p.e2_turnaround_time)*inactive)
                model.addConstr(b['first_rank'] >= a['last_rank']+1-(limit+1)*inactive)
    context['pool_choices'], context['pool_slots'] = choices, slots
    model.update()
    check()


def extract_pool(problem, context, initial, pools, backend):
    trips = []
    for i, old in enumerate(initial['e2_trips']):
        selected = [j for j, v in context['pool_choices'][i].items() if v.X > .5]
        if not selected:
            continue
        if len(selected) != 1:
            raise RuntimeError('Route-pool extraction has multiple routes in one physical slot')
        row, c = context['pool_slots'][i], pools[i][selected[0]]
        events = [dict(order_id=oid, kind=kind, quantity=problem.order_map[oid].count,
                       start=int(round(context['order_times'][oid, kind].X))) for oid, kind in c['tasks']]
        trips.append(dict(trip_id=old['trip_id'], satellite_id=old['satellite_id'],
            vehicle_id=old['vehicle_id'], trip_index=old['trip_index'], events=events,
            departure=int(round(row['start'].X)), return_complete=int(round(row['end'].X))))
    return extract_routes(problem, context, backend, trips)


def _reoptimize_once(problem, initial_result, *, time_limit=10, seed=42, verbose=False,
                     max_columns=128, transfers=True, diagnostics_dir=None):
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    if not isinstance(max_columns, int) or isinstance(max_columns, bool) or max_columns < 1:
        raise ValueError('max_columns must be a positive integer')
    if (problem.model_number != 5 or problem.free_assignment or not problem.reusable
            or len(problem.operating_policy.batches) <= 1):
        raise ValueError('Route-pool reoptimization requires fixed reusable multibatch M5')
    begun, model = time.perf_counter(), None
    deadline = begun+time_limit
    before = audit_solution(problem, initial_result)
    if not before['valid']:
        raise ValueError('Initial result must pass the full M5 independent audit')
    backend = 'patterns' if len({o.satellite for o in problem.orders}) <= 4 and problem.operating_policy.e1_turnaround_time == 0 else 'indexed'
    result = deepcopy(initial_result)
    for field in ('audit', 'cutoff_snapshots', 'depot_releases'):
        result.pop(field, None)
    result['info'] = dict(status='FEASIBLE', objective=before['objective'], best_bound=None, mip_gap=None,
        proven_optimal=False, proven_infeasible=False, proof_scope='restricted_E2_route_pool_primal',
        upper_before=before['objective'], route_pool_backend=backend, route_pool_subproblem_optimal=False,
        route_pool_subproblem_bound=None, route_pool_complete=False, route_pool_transfers=bool(transfers))
    status = 'TIME_LIMIT_BUILD'
    try:
        import gurobipy as gp
        from gurobipy import GRB
        pools = candidate_pools(problem, initial_result, deadline=begun+time_limit*.2,
                                max_columns=max_columns, transfers=transfers)
        result['info'].update(route_pool_columns=sum(map(len, pools)), route_pool_candidates=[len(row) for row in pools])
        if time.perf_counter() >= deadline:
            raise TimeoutError
        if backend == 'patterns':
            model, context = build_e1_routes(problem, deadline=deadline, verbose=verbose)
            set_route_projection(problem, context, initial_result)
        else:
            model, context = build_e1_inventory(problem, deadline=deadline, verbose=verbose)
            set_e1_projection(problem, context, initial_result)
        add_route_pool(problem, model, context, initial_result, pools, deadline=deadline)
        model.Params.Seed = int(seed)
        model.Params.TimeLimit = max(0, deadline-time.perf_counter())
        model.optimize()
        status = int(model.Status)
        if diagnostics_dir is not None:
            import json
            path = Path(diagnostics_dir)
            path.mkdir(parents=True, exist_ok=True)
            model.write(str(path/'route_pool.mps'))
            (path/'columns.json').write_text(json.dumps(pools, ensure_ascii=False, indent=2)+'\n')
        try:
            raw = model.ObjBound
        except (gp.GurobiError, AttributeError):
            raw = None
        result['info'].update(route_pool_subproblem_optimal=status == GRB.OPTIMAL,
            route_pool_subproblem_bound=float(raw) if raw is not None and math.isfinite(raw) else None,
            route_pool_variables=model.NumVars, route_pool_constraints=model.NumConstrs, route_pool_work=model.Work)
        if model.SolCount:
            candidate = extract_pool(problem, context, initial_result, pools, backend)
            audited = audit_solution(problem, candidate)
            if not audited['valid']:
                raise RuntimeError(f'Route pool failed full cycle audit: {audited["errors"]}')
            if not math.isclose(audited['objective'], model.ObjVal, rel_tol=1e-7, abs_tol=1e-5):
                raise RuntimeError('Route-pool objective differs from full audited distance')
            if audited['objective'] < before['objective']-1e-6:
                candidate['info'] = result['info']
                result = candidate
    except TimeoutError:
        pass
    finally:
        if model is not None:
            model.dispose()
    audited = audit_solution(problem, {**result, 'info': {**result['info'], 'objective': None}})
    if not audited['valid']:
        raise RuntimeError('Returned route-pool incumbent is invalid')
    result['info'].update(objective=audited['objective'], route_pool_status=status,
        route_pool_improved=audited['objective'] < before['objective']-1e-6, runtime=time.perf_counter()-begun)
    result['audit'] = audited
    if diagnostics_dir is not None:
        import json
        path = Path(diagnostics_dir)
        path.mkdir(parents=True, exist_ok=True)
        for name, payload in [('initial_result.json', initial_result), ('output_result.json', result)]:
            (path/name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    return result


def reoptimize_route_pool(problem, initial_result, *, time_limit=10, seed=42, verbose=False,
                          max_columns=128, transfers=True, max_passes=4, diagnostics_dir=None):
    """Rebuild the candidate neighborhood after each audited improvement.

    All passes share the original total budget. Stop at an unchanged upper
    bound; absence of improvement proves nothing outside the generated pool.
    """
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    if not isinstance(max_passes, int) or isinstance(max_passes, bool) or max_passes < 1:
        raise ValueError('max_passes must be a positive integer')
    begun, result, trace = time.perf_counter(), initial_result, []
    for index in range(max_passes):
        remaining = max(1e-12, time_limit-(time.perf_counter()-begun))
        result = _reoptimize_once(problem, result, time_limit=remaining, seed=seed,
            verbose=verbose, max_columns=max_columns, transfers=transfers,
            diagnostics_dir=Path(diagnostics_dir)/f'pass_{index}' if diagnostics_dir is not None else None)
        info = result['info']
        trace.append(dict(pass_index=index, upper_before=info['upper_before'], upper_after=info['objective'],
            subproblem_bound=info['route_pool_subproblem_bound'], subproblem_optimal=info['route_pool_subproblem_optimal'],
            status=info['route_pool_status'], columns=info.get('route_pool_columns'), runtime=info['runtime']))
        if not info['route_pool_improved'] or time.perf_counter()-begun >= time_limit:
            break
    result['info'].update(upper_before=trace[0]['upper_before'],
        route_pool_improved=result['info']['objective'] < trace[0]['upper_before']-1e-6,
        route_pool_passes=trace, runtime=time.perf_counter()-begun)
    return result
