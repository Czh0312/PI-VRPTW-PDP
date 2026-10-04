"""Joint COMPLETE E2 route coverage and E1 cutoff/clean-stock relaxation.

Only route task sets and necessary return-time bounds enter the E1 links.
The cheapest route's actual clock is NOT imposed: cost dominance need not
preserve actual supply, timing, or cross-route vehicle feasibility.
"""
from __future__ import annotations

import math
from copy import deepcopy
from pathlib import Path
import time

from models.routing.common.cycle.precheck import travel_lower_bounds


def cutoff_domains(problem, context, universe, travel):
    """Every actual trip returns after every task's necessary service/return."""
    if not universe['complete'] or not all(g['complete'] for g in universe['groups']):
        raise ValueError('Joint inventory requires complete E2 route universes')
    if 'timing_groups' in universe:
        if not all(g['complete'] for g in universe['timing_groups']):
            raise ValueError('Joint inventory requires complete timing frontiers')
        return [dict(column=r['column'], keys=tuple(tuple(k) for k in r['column']['tasks']),
            pickups=tuple(oid for oid, kind in r['column']['tasks'] if kind == 'p'),
            minimum_return_bound=r['column']['return_complete'], allowed_cutoffs=(r['cutoff'],),
            latest_departure=r['latest_departure'])
            for g in universe['timing_groups'] for r in g['columns']]
    p, site = problem.operating_policy, context['satellite']
    after = {n['key']: n['window'][0]+n['order'].service_time
             +travel['inward'][site][n['order'].customer]+p.e2_return_unload_time
             for n in context['nodes']}
    rows = []
    for group in universe['groups']:
        for column in group['columns']:
            keys = tuple(tuple(k) for k in column['tasks'])
            pickups = tuple(oid for oid, kind in keys if kind == 'p')
            earliest = max(after[k] for k in keys)
            allowed = tuple(b.id for b in p.batches if b.cutoff >= earliest) if pickups else (None,)
            if not allowed:
                raise ValueError('A complete feasible trip has no necessary return cutoff')
            rows.append(dict(column=column, keys=keys, pickups=pickups,
                             minimum_return_bound=int(earliest), allowed_cutoffs=allowed))
    return rows


def build_joint_inventory(problem, domains, *, deadline=None, verbose=False,
                          travel_bounds=None, distance_bounds=None, e1_bound=0.0, site_bounds=None):
    """Retain every cutoff-compatible column, and couple whole-trip pickups."""
    from gurobipy import GRB
    import gurobipy as gp
    p = problem.operating_policy
    travel = travel_bounds or travel_lower_bounds(problem)
    active = {o.satellite for o in problem.orders}
    if set(domains) != active:
        raise ValueError('Joint inventory requires complete domains for every active satellite')
    backend = 'patterns' if len(active) <= 4 else 'indexed'
    if backend == 'patterns':
        from .multibatch_e1_routes import build_e1_routes
        model, e1 = build_e1_routes(problem, deadline=deadline, verbose=verbose,
            travel_bounds=travel, distance_bounds=distance_bounds)
    else:
        from .multibatch_e1_inventory import build_e1_inventory
        model, e1 = build_e1_inventory(problem, deadline=deadline, verbose=verbose,
            travel_bounds=travel, distance_bounds=distance_bounds)
    def check():
        if deadline is not None and time.perf_counter() >= deadline:
            raise TimeoutError('Joint inventory build reached its original budget')
    try:
        e1_objective = model.getObjective()
        if e1_bound > 0:
            model.addConstr(e1_objective >= e1_bound, name='certified_E1_cost')
        link = {(o.id, b.id): model.addConstr(e1['allocation'][o.id, b.id] == 0,
                                            name=f'joint_cutoff_{o.id}_{b.id}')
                for o in problem.orders for b in p.batches}
        routes, all_choices, site_costs = {}, [], {}
        for site_index, site in enumerate(sorted(active)):
            check()
            context, universe = domains[site]
            rows = cutoff_domains(problem, context, universe, travel)
            timed = 'timing_groups' in universe
            levels = {}
            if timed and backend == 'patterns':
                from .multibatch_route_timing import compress_supply_profiles
                levels = {n['key'][0]: sorted({r['supply'][site]+p.e2_dispatch_prep_time
                    for r in e1['patterns'] if r['batch'] == n['order'].batch and site in r['delivery']})
                    for n in context['nodes'] if n['key'][1] == 'd'}
                rows = compress_supply_profiles(rows, levels)
            cover = {n['key']: model.addConstr(gp.LinExpr() == 1, name=f'joint_cover_{site_index}_{i}')
                     for i, n in enumerate(context['nodes'])}
            fleet = model.addConstr(gp.LinExpr() <= context['maximum_trips'], name=f'joint_trip_limit_{site_index}')
            cost = model.addConstr(gp.LinExpr() >= (site_bounds or {}).get(site, 0.0), name=f'certified_E2_cost_{site_index}')
            supplies = e1.get('supplies', e1.get('deliveries'))
            supply = {n['key']: model.addConstr(supplies[site, n['order'].batch]
                <= -p.e2_dispatch_prep_time, name=f'joint_supply_{site_index}_{i}')
                for i, n in enumerate(context['nodes']) if n['key'][1] == 'd'} if timed and backend == 'indexed' else {}
            cliques = {}
            if levels:
                for i, n in enumerate(context['nodes']):
                    if n['key'][1] != 'd':
                        continue
                    for level_index, value in enumerate(levels[n['key'][0]][1:], 1):
                        late = gp.quicksum(e1['chosen'][j] for j, r in enumerate(e1['patterns'])
                            if r['batch'] == n['order'].batch and site in r['delivery']
                            and r['supply'][site]+p.e2_dispatch_prep_time >= value)
                        cliques[n['key'], value] = model.addConstr(late <= 1,
                            name=f'joint_supply_clique_{site_index}_{i}_{level_index}')
            choices, site_cost = {}, gp.LinExpr()
            for i, row in enumerate(rows):
                if i % 256 == 0:
                    check()
                for batch_index, bid in enumerate(row['allowed_cutoffs']):
                    coefficients = [1]*len(row['keys'])+[1, row['column']['cost']]
                    constraints = [cover[k] for k in row['keys']]+[fleet, cost]
                    if bid is not None:
                        coefficients += [-1]*len(row['pickups'])
                        constraints += [link[oid, bid] for oid in row['pickups']]
                    delivered = [key for key in row['keys'] if key[1] == 'd'] if supply else []
                    coefficients += [-row['latest_departure']]*len(delivered) if delivered else []
                    constraints += [supply[key] for key in delivered]
                    bad = [cliques[key, value] for key in row['keys'] if key[1] == 'd'
                           for value in levels.get(key[0], [])[1:] if row['latest_departure'] < value]
                    coefficients += [1]*len(bad)
                    constraints += bad
                    var = model.addVar(vtype=GRB.BINARY, obj=row['column']['cost'],
                        column=gp.Column(coefficients, constraints), name=f'joint_route_{site_index}_{i}_{batch_index}')
                    choices[i, bid] = var
                    all_choices.append(var)
                    site_cost += row['column']['cost']*var
            routes[site] = dict(context=context, rows=rows, choices=choices)
            site_costs[site] = site_cost
        model.update()
        check()
        return model, dict(e1=e1, backend=backend, routes=routes, e1_objective=e1_objective,
                           site_costs=site_costs, choices=all_choices, link=link)
    except Exception:
        model.dispose()
        raise


def set_joint_projection(problem, context, initial_result, *, fix=False):
    """Project the original task sets/cutoffs, retaining original order times."""
    if context['backend'] == 'patterns':
        from .multibatch_e1_routes import set_route_projection
        set_route_projection(problem, context['e1'], initial_result, fix=fix)
    else:
        from .multibatch_e1_inventory import set_e1_projection
        set_e1_projection(problem, context['e1'], initial_result, fix=fix)
    def put(var, value):
        var.Start = value
        if fix:
            var.LB = var.UB = value
    for trip in initial_result['e2_trips']:
        for event in trip['events']:
            put(context['e1']['order_times'][event['order_id'], event['kind']], event['start'])
    selected, projected_cost = {}, 0.0
    projected_supply = {}
    if context['backend'] == 'patterns':
        patterns = context['e1']['patterns']
        lookup = {(r['batch'], r['delivery'], r['pickup']): r for r in patterns}
        for trip in initial_result['e1_trips']:
            pattern = lookup[trip['batch_id'], tuple(e['site_id'] for e in trip['events'] if e['kind'] == 'd'),
                             tuple(e['site_id'] for e in trip['events'] if e['kind'] == 'p')]
            projected_supply.update({(s, trip['batch_id']): value+problem.operating_policy.e2_dispatch_prep_time
                                     for s, value in pattern['supply'].items()})
    for site, data in context['routes'].items():
        lookup = {}
        for i, row in enumerate(data['rows']):
            lookup.setdefault(frozenset(row['keys']), []).append(i)
        chosen = set()
        for trip in initial_result['e2_trips']:
            if trip['satellite_id'] != site:
                continue
            task_set = frozenset((e['order_id'], e['kind']) for e in trip['events'])
            bid = next(b.id for b in problem.operating_policy.batches if trip['return_complete'] <= b.cutoff) \
                if any(kind == 'p' for _, kind in task_set) else None
            delivery = next((problem.order_map[oid].batch for oid, kind in task_set if kind == 'd'), None)
            required_departure = projected_supply.get((site, delivery), trip['departure'])
            candidates = [i for i in lookup[task_set] if (i, bid) in data['choices']
                and (data['rows'][i].get('latest_departure') is None
                     or data['rows'][i]['latest_departure'] >= required_departure)]
            if not candidates:
                raise ValueError('Full-M5 trip failed to project to a joint cutoff column')
            i = min(candidates, key=lambda j: data['rows'][j]['column']['cost'])
            chosen.add((i, bid))
            projected_cost += data['rows'][i]['column']['cost']
        for key, var in data['choices'].items():
            put(var, int(key in chosen))
        selected[site] = len(chosen)
    return dict(mapped_trips=selected, projected_e2_cost=projected_cost)


def solve_joint_inventory(problem, domains, *, deadline, initial_result=None, seed=42,
                          verbose=False, travel_bounds=None, distance_bounds=None,
                          e1_bound=0.0, site_bounds=None, fallback_bound=0.0, diagnostics_dir=None):
    """Return a solver LOWER bound on joint cost; never a full-M5 upper bound."""
    from gurobipy import GRB
    import gurobipy as gp
    begun, model, context = time.perf_counter(), None, None
    result = dict(bound=fallback_bound, raw_bound=None, margin=0.0, status='TIME_LIMIT_BUILD',
        scope='M5_joint_E1_inventory_and_integer_E2_cutoff_cover_relaxation',
        proven_optimal=False, proven_infeasible=False, relaxation_optimal=False,
        relaxation_infeasible=False, relaxation_objective=None, initial_projection=None,
        complete_route_universes=True, runtime=0.0, selected_columns=[])
    try:
        model, context = build_joint_inventory(problem, domains, deadline=deadline, verbose=verbose,
            travel_bounds=travel_bounds, distance_bounds=distance_bounds, e1_bound=e1_bound, site_bounds=site_bounds)
        model.Params.Seed = int(seed)
        model.Params.IntFeasTol = model.Params.FeasibilityTol = 1e-9
        timed = any('timing_groups' in universe for _, universe in domains.values())
        if timed:
            # A full audited M5 start already supplies a valid incumbent;
            # this stage's purpose is a certified LOWER bound.
            model.Params.Presolve = 1
            model.Params.Heuristics = 0
        if initial_result is not None:
            result['initial_projection'] = set_joint_projection(problem, context, initial_result)
        model.Params.TimeLimit = max(0, deadline-time.perf_counter())
        model.optimize()
        try:
            raw = float(model.ObjBound)
        except (gp.GurobiError, AttributeError):
            raw = math.nan
        if math.isfinite(raw) and model.Status != GRB.INFEASIBLE:
            margin = 1e-7*max(1.0, abs(raw))
            result.update(raw_bound=raw, margin=margin, bound=max(fallback_bound, max(0, raw-margin)))
        result.update(status=int(model.Status), relaxation_optimal=model.Status == GRB.OPTIMAL,
            relaxation_infeasible=model.Status == GRB.INFEASIBLE,
            variables=model.NumVars, constraints=model.NumConstrs, general_constraints=model.NumGenConstrs,
            route_choices=len(context['choices']), backend=context['backend'], nodes=model.NodeCount)
        result['timing_envelopes'] = timed
        if model.SolCount:
            result['relaxation_objective'] = float(model.ObjVal)
            result['selected_columns'] = [dict(satellite=site, column_index=i, cutoff=bid,
                tasks=data['rows'][i]['column']['tasks'], cost=data['rows'][i]['column']['cost'])
                for site, data in context['routes'].items() for (i, bid), v in data['choices'].items() if v.X > .5]
            result['candidate_covers'] = []
            for number in range(min(5, model.SolCount)):
                model.Params.SolutionNumber = number
                result['candidate_covers'].append(dict(objective=float(model.PoolObjVal), columns=[
                    dict(satellite=site, tasks=data['rows'][i]['column']['tasks'], cost=data['rows'][i]['column']['cost'])
                    for site, data in context['routes'].items() for (i, bid), v in data['choices'].items() if v.Xn > .5]))
            model.Params.SolutionNumber = 0
        if diagnostics_dir is not None:
            path = Path(diagnostics_dir)
            path.mkdir(parents=True, exist_ok=True)
            from .multibatch_route_timing import write_compressed_model
            write_compressed_model(model, path/'joint_inventory.mps.gz')
        return result
    except TimeoutError:
        return result
    finally:
        result['runtime'] = time.perf_counter()-begun
        if model is not None:
            model.dispose()


def recover_joint_schedule(problem, initial_result, joint, *, deadline, seed=42,
                           verbose=False, diagnostics_dir=None):
    """Try relaxed columns in an actual physical-slot master; audit any gain."""
    from models.routing.common.cycle.audit import audit_solution
    from .multibatch_route_pricing import evaluate_column, route_context
    from .multibatch_route_pool import add_route_pool, extract_pool
    import gurobipy as gp
    begun, model = time.perf_counter(), None
    audit = audit_solution(problem, initial_result)
    if not audit['valid']:
        raise ValueError('Schedule recovery requires an audited full-M5 incumbent')
    result = deepcopy(initial_result)
    metadata = dict(improved=False, status='TIME_LIMIT_BUILD', objective=audit['objective'],
        upper_before=audit['objective'], runtime=0.0, proof_scope='restricted_joint_column_schedule_recovery')
    try:
        if time.perf_counter() >= deadline:
            raise TimeoutError
        contexts = {s: route_context(problem, s) for s in {o.satellite for o in problem.orders}}
        by_site = {s: {} for s in contexts}
        original = []
        for trip in initial_result['e2_trips']:
            column = evaluate_column(problem, contexts[trip['satellite_id']],
                [(e['order_id'], e['kind']) for e in trip['events']])
            if column is None:
                raise RuntimeError('Audited original route failed recovery projection')
            original.append(column)
            by_site[trip['satellite_id']][column['tasks']] = column
        proposals = list(joint['selected_columns'])
        proposals += [row for cover in joint.get('candidate_covers', []) for row in cover['columns']]
        for row in proposals:
            column = evaluate_column(problem, contexts[row['satellite']], row['tasks'])
            if column is not None:
                by_site[row['satellite']][column['tasks']] = column
        pools = [[own]+[c for k, c in by_site[t['satellite_id']].items() if k != own['tasks']]
                 for t, own in zip(initial_result['e2_trips'], original)]
        active = len(contexts)
        backend = 'patterns' if active <= 4 and problem.operating_policy.e1_turnaround_time == 0 else 'indexed'
        if backend == 'patterns':
            from .multibatch_e1_routes import build_e1_routes, set_route_projection
            model, context = build_e1_routes(problem, deadline=deadline, verbose=verbose)
            set_route_projection(problem, context, initial_result)
        else:
            from .multibatch_e1_inventory import build_e1_inventory, set_e1_projection
            model, context = build_e1_inventory(problem, deadline=deadline, verbose=verbose)
            set_e1_projection(problem, context, initial_result)
        add_route_pool(problem, model, context, initial_result, pools, deadline=deadline)
        model.Params.Seed = int(seed)
        model.Params.TimeLimit = max(0, deadline-time.perf_counter())
        model.optimize()
        metadata.update(status=int(model.Status), backend=backend, columns=sum(map(len, pools)))
        if model.SolCount:
            candidate = extract_pool(problem, context, initial_result, pools, backend)
            checked = audit_solution(problem, candidate)
            if not checked['valid'] or not math.isclose(checked['objective'], model.ObjVal, rel_tol=1e-7, abs_tol=1e-5):
                raise RuntimeError('Joint-column recovery failed full schedule/distance audit')
            if checked['objective'] < audit['objective']-1e-6:
                result, audit = candidate, checked
                metadata['improved'] = True
        try:
            local_bound = float(model.ObjBound)
        except (gp.GurobiError, AttributeError):
            local_bound = math.nan
        result['info'] = dict(status='FEASIBLE', objective=audit['objective'], best_bound=None, mip_gap=None,
            proven_optimal=False, proven_infeasible=False, proof_scope=metadata['proof_scope'],
            route_pool_backend=backend, route_pool_complete=False,
            route_pool_subproblem_optimal=model.Status == 2,
            route_pool_subproblem_bound=local_bound if math.isfinite(local_bound) else None,
            route_pool_variables=model.NumVars, route_pool_constraints=model.NumConstrs)
        if diagnostics_dir is not None:
            import json
            path = Path(diagnostics_dir)
            path.mkdir(parents=True, exist_ok=True)
            model.write(str(path/'route_pool.mps'))
            for name, value in [('columns.json', pools), ('initial_result.json', initial_result), ('output_result.json', result)]:
                (path/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    except TimeoutError:
        pass
    finally:
        if model is not None:
            model.dispose()
        metadata.update(objective=audit['objective'], runtime=time.perf_counter()-begun)
    return result, metadata


def joint_route_inventory_certificate(problem, *, time_limit=60, initial_result=None,
        seed=42, joint_time_limit=25, recovery_time_limit=5, e1_time_limit=10,
        integer_cover_time_limit=15, enumeration_time_limit=15, enumeration_max_states=2000000,
        verbose=False, diagnostics_dir=None, timing_envelopes=False):
    """Independent bounds, joint cutoff coupling, then audited schedule recovery."""
    from models.routing.common.cycle.audit import audit_solution
    from .multibatch_bounds import distance_certificate
    from .multibatch_e1_inventory import e1_inventory_certificate
    from .multibatch_route_pricing import route_context
    from .multibatch_route_enumeration import enumerate_route_universe, enumerated_route_cover, enumeration_stats
    if timing_envelopes:
        from .multibatch_route_timing import enumerate_timed_route_universe
        enumerate_route_universe = enumerate_timed_route_universe
    from .multibatch_route_cg import site_route_cover
    if not isinstance(timing_envelopes, bool):
        raise ValueError('timing_envelopes must be boolean')
    if problem.model_number != 5 or problem.free_assignment or not problem.reusable or len(problem.operating_policy.batches) <= 1:
        raise ValueError('Joint route inventory requires fixed reusable multibatch M5')
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    for name, value in [('joint_time_limit', joint_time_limit), ('recovery_time_limit', recovery_time_limit),
                        ('e1_time_limit', e1_time_limit), ('integer_cover_time_limit', integer_cover_time_limit)]:
        if not math.isfinite(value) or value < 0:
            raise ValueError(name+' must be finite and nonnegative')
    if not math.isfinite(enumeration_time_limit) or enumeration_time_limit <= 0:
        raise ValueError('enumeration_time_limit must be finite and positive')
    if not isinstance(enumeration_max_states, int) or isinstance(enumeration_max_states, bool) or enumeration_max_states < 1:
        raise ValueError('enumeration_max_states must be a positive integer')
    if recovery_time_limit > 0 and joint_time_limit == 0:
        raise ValueError('Schedule recovery requires a joint stage')
    begun = time.perf_counter()
    deadline = begun+time_limit
    audited = audit_solution(problem, initial_result) if initial_result is not None else None
    if audited is not None and not audited['valid']:
        raise ValueError('Initial result must pass the full M5 independent audit')
    reference_deadline = deadline-min(time_limit/3, joint_time_limit+recovery_time_limit)
    path = Path(diagnostics_dir) if diagnostics_dir is not None else None
    payload = dict(bound=0.0, upper_bound=audited['objective'] if audited else None,
        gap=None, reference_certificate=None, joint_certificate=None, recovery=None,
        result=deepcopy(initial_result), scope='M5_joint_route_inventory_bounds_and_audited_primal',
        proven_optimal=False, proven_infeasible=False, status='TIME_LIMIT_BUILD')
    try:
        baseline = distance_certificate(problem, deadline=reference_deadline)
        travel = travel_lower_bounds(problem)
        e1 = None
        if e1_time_limit > 0 and time.perf_counter() < reference_deadline:
            e1 = e1_inventory_certificate(problem, time_limit=min(e1_time_limit, (reference_deadline-time.perf_counter())/3),
                initial_result=initial_result, seed=seed, verbose=verbose, travel_bounds=travel, distance_bounds=baseline,
                diagnostics_path=path/'e1_inventory.mps' if path else None)
        domains, sites = {}, []
        contexts = sorted((route_context(problem, s, travel) for s in problem.satellites),
                          key=lambda c: (len(c['nodes']), c['satellite']))
        for index, context in enumerate(contexts):
            now = time.perf_counter()
            site_deadline = min(reference_deadline, now+max(0, reference_deadline-now)/(len(contexts)-index))
            export = path/f'site_{index}_best_pricing.mps' if path else None
            if context['nodes'] and now < site_deadline:
                universe = enumerate_route_universe(problem, context,
                    deadline=min(site_deadline, now+min(enumeration_time_limit,
                        (site_deadline-now)*(.85 if timing_envelopes else .5))),
                    max_states=enumeration_max_states)
                if universe['complete']:
                    domains[context['satellite']] = context, universe
                    reference_universe = {k: v for k, v in universe.items() if k != 'timing_groups'}
                    row = enumerated_route_cover(problem, context, reference_universe, deadline=site_deadline,
                        verbose=verbose, export_path=export, integer_cover_time_limit=integer_cover_time_limit,
                        initial_result=initial_result, seed=seed)
                    if timing_envelopes and export is not None:
                        import gzip
                        import json
                        export.parent.mkdir(parents=True, exist_ok=True)
                        timing_data = {k: universe[k] for k in ('complete', 'states', 'timing_columns', 'timing_groups')}
                        encoded = json.dumps(timing_data, ensure_ascii=False, allow_nan=False,
                                             separators=(',', ':')).encode('utf-8')
                        with gzip.open(export.with_name(export.stem+'_timing.json.gz'), 'wb', compresslevel=1) as stream:
                            stream.write(encoded)
                        del encoded
                else:
                    row = site_route_cover(problem, context, deadline=site_deadline, initial_result=initial_result,
                        seed=seed, verbose=verbose, pricing_export_path=export)
                    row['enumeration'] = enumeration_stats({k: v for k, v in universe.items() if k != 'timing_groups'})
            else:
                row = site_route_cover(problem, context, deadline=site_deadline, initial_result=initial_result,
                    seed=seed, verbose=verbose, pricing_export_path=export)
            sites.append(row)
        e1_distance = math.fsum(max(0, r['bound']-1e-7*max(1, r['bound']))
                               for r in baseline['components'] if r['echelon'] == 'E1')
        e1_bound = max(e1_distance, e1['bound'] if e1 else 0)
        distance_sites = {r['satellite']: max(0, r['bound']-1e-7*max(1, r['bound']))
                          for r in baseline['components'] if r['echelon'] == 'E2'}
        components = [dict(satellite=r['satellite'], route_cover_bound=r['bound'],
            distance_bound=distance_sites.get(r['satellite'], 0), bound=max(r['bound'], distance_sites.get(r['satellite'], 0))) for r in sites]
        raw = e1_bound+math.fsum(c['bound'] for c in components)
        reference_bound = max(baseline['bound'], max(0, raw-1e-7*max(1, raw)))
        reference = dict(bound=reference_bound, raw_bound=raw, e1_bound=e1_bound, distance_e1_bound=e1_distance,
            e1_certificate=e1, components=components, sites=sites, distance_certificate=baseline,
            scope='M5_relaxed_E2_route_cover_plus_E1_certificate', proven_optimal=False, proven_infeasible=False,
            runtime=time.perf_counter()-begun, time_limit=reference_deadline-begun)
        payload.update(bound=reference_bound, reference_certificate=reference, status='SEPARATE_CERTIFIED_BOUND')
        if joint_time_limit > 0:
            if set(domains) == {o.satellite for o in problem.orders}:
                joint_deadline = min(deadline, time.perf_counter()+joint_time_limit,
                    deadline-min(recovery_time_limit, max(0, deadline-time.perf_counter())/3))
                joint = solve_joint_inventory(problem, domains, deadline=joint_deadline, initial_result=initial_result,
                    seed=seed, verbose=verbose, travel_bounds=travel, distance_bounds=baseline, e1_bound=e1_bound,
                    site_bounds={r['satellite']: r['bound'] for r in sites}, fallback_bound=reference_bound,
                    diagnostics_dir=path/'joint' if path else None)
                payload.update(bound=joint['bound'], joint_certificate=joint, status='JOINT_CERTIFIED_BOUND')
                if initial_result is not None and recovery_time_limit > 0 and joint['selected_columns']:
                    result, recovered = recover_joint_schedule(problem, initial_result, joint,
                        deadline=min(deadline, time.perf_counter()+recovery_time_limit), seed=seed, verbose=verbose,
                        diagnostics_dir=path/'recovery' if path else None)
                    checked = audit_solution(problem, result)
                    if not checked['valid']:
                        raise RuntimeError('Returned joint recovery failed the full M5 audit')
                    payload.update(result=result, upper_bound=checked['objective'], recovery=recovered)
            else:
                payload['status'] = 'PARTIAL_ENUMERATION_SEPARATE_BOUND'
    except TimeoutError:
        pass
    if payload['upper_bound'] is not None:
        payload['gap'] = max(0, payload['upper_bound']-payload['bound'])/max(1e-9, abs(payload['upper_bound']))
    payload['runtime'] = time.perf_counter()-begun
    return payload
