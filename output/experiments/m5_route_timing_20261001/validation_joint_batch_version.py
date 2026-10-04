"""Verify joint matrices and original full-M5 projections without production builders."""
from collections import Counter
import gzip
import json
import math

from models.routing.common.cycle.audit import audit_solution
from validation_e1 import all_patterns
from validation_enumeration import independent_context
from validation_timing import pattern_supplies, independent_supply_compression


def close(a, b):
    assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6), (a, b)


def indicator(model, row):
    var, value, expression, sense, rhs = model.getGenConstrIndicator(row)
    return (var.VarName, value, sorted((expression.getVar(i).VarName, expression.getCoeff(i))
            for i in range(expression.size())), sense, rhs)


def verify_joint(problem, initial, reference, cert, directory, seed=42):
    import gurobipy as gp
    import numpy as np
    from gurobipy import GRB
    assert not cert['proven_optimal'] and not cert['proven_infeasible']
    assert cert['complete_route_universes'] and cert['backend'] == 'patterns'
    assert cert['raw_bound'] is not None and cert['bound'] >= reference['bound']
    close(cert['margin'], 1e-7*max(1, abs(cert['raw_bound'])))
    close(cert['bound'], max(reference['bound'], max(0, cert['raw_bound']-cert['margin'])))
    mps = directory/'joint/joint_inventory.mps.gz'
    model, base = gp.read(str(mps)), gp.read(str(directory/'e1_inventory.mps'))
    try:
        assert (model.NumVars, model.NumConstrs, model.NumGenConstrs) == (
            cert['variables'], cert['constraints'], cert['general_constraints'])
        n, r = base.NumVars, base.NumConstrs
        variables, constraints = model.getVars(), model.getConstrs()
        names = model.getAttr('VarName', variables)
        row_names = model.getAttr('ConstrName', constraints)
        row_index, variable_index = {v: i for i, v in enumerate(row_names)}, {v: i for i, v in enumerate(names)}
        assert names[:n] == base.getAttr('VarName') and row_names[:r] == base.getAttr('ConstrName')
        for attribute in ('VType', 'LB', 'UB', 'Obj'):
            original, joint = base.getAttr(attribute), model.getAttr(attribute, variables[:n])
            if attribute == 'VType': assert original == joint
            else: assert np.allclose(original, joint, rtol=1e-12, atol=1e-9)
        assert base.getAttr('Sense') == model.getAttr('Sense', constraints[:r])
        assert np.allclose(base.getAttr('RHS'), model.getAttr('RHS', constraints[:r]), rtol=1e-12, atol=1e-9)
        def entries(m, constraint):
            expression = m.getRow(constraint)
            return [(expression.getVar(i).index, expression.getCoeff(i)) for i in range(expression.size())]
        for original, joint in zip(base.getConstrs(), constraints[:r]):
            assert entries(base, original) == entries(model, joint)
        assert [indicator(base, g) for g in base.getGenConstrs()] == [indicator(model, g) for g in model.getGenConstrs()]
        # Reconstruct every added linear coefficient from raw input and all
        # serialized complete columns. Shortest times come from Floyd-Warshall.
        expected = [[] for _ in constraints[r:]]
        expected_rows = []
        def add(row, col, coefficient):
            if coefficient:
                expected[row-r].append((col, coefficient))
        def row(name, sense, rhs):
            expected_rows.append(name)
            i = row_index[name]
            assert constraints[i].Sense == sense
            close(constraints[i].RHS, rhs)
            return i
        if reference['e1_bound'] > 0:
            i = row('certified_E1_cost', '>', reference['e1_bound'])
            for j, cost in enumerate(base.getAttr('Obj')): add(i, j, cost)
        links = {}
        for o in problem.orders:
            for b in problem.operating_policy.batches:
                i = row(f'joint_cutoff_{o.id}_{b.id}', '=', 0)
                links[o.id, b.id] = i
                add(i, variable_index[f'cutoff_{o.id}_{b.id}'], 1)
        sites = {s['satellite']: (i, s) for i, s in enumerate(reference['sites'])}
        next_column, selected, mapping = n, set(), {}
        selected_rows = {(x['satellite'], x['column_index'], x['cutoff']): x for x in cert['selected_columns']}
        initial_cost = 0.0
        patterns = all_patterns(problem)
        e1_supplies = pattern_supplies(problem, patterns)
        timed = cert.get('timing_envelopes', False)
        for site_index, site in enumerate(sorted({o.satellite for o in problem.orders})):
            source_index, site_cert = sites[site]
            with gzip.open(directory/f'site_{source_index}_best_pricing.json.gz', 'rt') as stream:
                universe = json.load(stream)
            assert universe['complete'] and all(g['complete'] for g in universe['groups'])
            context = independent_context(problem, site)
            keys = [node['key'] for node in context['nodes']]
            necessary = {node['key']: node['low']+node['service']+node['min_return'] for node in context['nodes']}
            covers = {k: row(f'joint_cover_{site_index}_{i}', '=', 1) for i, k in enumerate(keys)}
            K = min(len(keys), problem.fleet.e2_per_sat_count*problem.operating_policy.e2_max_trips_per_vehicle)
            fleet = row(f'joint_trip_limit_{site_index}', '<', K)
            site_cost = row(f'certified_E2_cost_{site_index}', '>', site_cert['bound'])
            if timed:
                with gzip.open(directory/f'site_{source_index}_best_pricing_timing.json.gz', 'rt') as stream:
                    frontier = json.load(stream)
                assert frontier['complete'] and all(g['complete'] for g in frontier['timing_groups'])
                raw_records = [record for g in frontier['timing_groups'] for record in g['columns']]
                records, levels = independent_supply_compression(raw_records, context['nodes'], site, patterns, e1_supplies)
                cliques = {}
                for i, node in enumerate(context['nodes']):
                    if node['key'][1] != 'd': continue
                    for level_index, value in enumerate(levels[node['key'][0]][1:], 1):
                        constraint = row(f'joint_supply_clique_{site_index}_{i}_{level_index}', '<', 1)
                        cliques[node['key'], value] = constraint
                        for j, pattern in enumerate(patterns):
                            if pattern['batch'] == node['batch'] and site in pattern['delivery'] and e1_supplies[j][site] >= value:
                                add(constraint, variable_index[f'route[{j}]'], 1)
            else:
                records = [dict(column=column, latest_departure=None) for g in universe['groups'] for column in g['columns']]
                cliques = {}
            original_candidates = {}
            for trip in initial['e2_trips']:
                if trip['satellite_id'] != site: continue
                taskset = frozenset((e['order_id'], e['kind']) for e in trip['events'])
                cutoff = next(b.id for b in problem.operating_policy.batches if trip['return_complete'] <= b.cutoff) \
                    if any(kind == 'p' for _, kind in taskset) else None
                delivery = next((problem.order_map[oid].batch for oid, kind in taskset if kind == 'd'), None)
                required = trip['departure']
                if timed and delivery is not None:
                    e1_trip = next(t for t in initial['e1_trips'] if t['batch_id'] == delivery and any(e['kind'] == 'd' and e['site_id'] == site for e in t['events']))
                    index = next(i for i, pattern in enumerate(patterns) if pattern['batch'] == delivery
                        and pattern['delivery'] == tuple(e['site_id'] for e in e1_trip['events'] if e['kind'] == 'd')
                        and pattern['pickup'] == tuple(e['site_id'] for e in e1_trip['events'] if e['kind'] == 'p'))
                    required = e1_supplies[index][site]
                original_candidates[taskset, cutoff] = dict(trip=trip, required=required, choices=[])
            for i, record in enumerate(records):
                column = record['column']
                tasks = tuple(tuple(k) for k in column['tasks'])
                pickups = [oid for oid, kind in tasks if kind == 'p']
                lower_return = max(necessary[k] for k in tasks)
                allowed = [record['cutoff']] if timed else \
                    ([b.id for b in problem.operating_policy.batches if b.cutoff >= lower_return] if pickups else [None])
                assert allowed
                for batch_index, cutoff in enumerate(allowed):
                    j = next_column
                    next_column += 1
                    assert names[j] == f'joint_route_{site_index}_{i}_{batch_index}'
                    assert variables[j].VType == (GRB.CONTINUOUS if timed else GRB.BINARY) and variables[j].LB == 0 and variables[j].UB == 1
                    close(variables[j].Obj, column['cost'])
                    for key in tasks: add(covers[key], j, 1)
                    add(fleet, j, 1); add(site_cost, j, column['cost'])
                    for oid in pickups: add(links[oid, cutoff], j, -1)
                    for (key, value), constraint in cliques.items():
                        if key in tasks and record['latest_departure'] < value: add(constraint, j, 1)
                    mapping[site, i, cutoff] = j
                    original = original_candidates.get((frozenset(tasks), cutoff))
                    if original is not None and (record['latest_departure'] is None or record['latest_departure'] >= original['required']):
                        original['choices'].append((column['cost'], j))
                    if (site, i, cutoff) in selected_rows:
                        saved = selected_rows[site, i, cutoff]
                        assert tuple(tuple(k) for k in saved['tasks']) == tasks
                        close(saved['cost'], column['cost'])
            for original in original_candidates.values():
                assert original['choices']
                cost, index = min(original['choices'], key=lambda x: x[0])
                selected.add(index); initial_cost += cost
        assert next_column == model.NumVars and next_column-n == cert['route_choices']
        assert expected_rows == row_names[r:]
        for constraint, expected_entries in zip(constraints[r:], expected):
            observed = entries(model, constraint)
            assert len(observed) == len(expected_entries), constraint.ConstrName
            for (a, x), (b, y) in zip(observed, expected_entries):
                assert a == b, (constraint.ConstrName, a, b)
                close(x, y)
        assert set(selected_rows) <= mapping.keys()
        del expected, mapping
        for site in sites:
            columns = [x for x in cert['selected_columns'] if x['satellite'] == site]
            if not columns: continue
            counts = {node['key']: 0.0 for node in independent_context(problem, site)['nodes']}
            for x in columns:
                for key in x['tasks']: counts[tuple(key)] += x.get('value', 1.0)
            assert all(abs(value-1) <= 1e-5 for value in counts.values())
        # Build the original warm start independently, then replay ObjBound.
        starts = {}
        patterns = all_patterns(problem)
        lookup = {(p['batch'], p['delivery'], p['pickup']): i for i, p in enumerate(patterns)}
        chosen, quantities = set(), {}
        for trip in initial['e1_trips']:
            i = lookup[trip['batch_id'], tuple(e['site_id'] for e in trip['events'] if e['kind'] == 'd'),
                       tuple(e['site_id'] for e in trip['events'] if e['kind'] == 'p')]
            chosen.add(i)
            quantities.update({(i, e['site_id']): e['quantity'] for e in trip['events'] if e['kind'] == 'p'})
        for i, pattern in enumerate(patterns):
            starts[f'route[{i}]'] = int(i in chosen)
            for site in pattern['pickup']: starts[f'quantity_{i}_{site}'] = quantities.get((i, site), 0)
        for trip in initial['e2_trips']:
            for event in trip['events']:
                oid, kind = event['order_id'], event['kind']
                starts[('delivery_' if kind == 'd' else 'pickup_time_')+oid] = event['start']
                if kind == 'p':
                    starts['e2_return_'+oid] = trip['return_complete']
                    first = next(b.id for b in problem.operating_policy.batches if trip['return_complete'] <= b.cutoff)
                    for b in problem.operating_policy.batches: starts[f'cutoff_{oid}_{b.id}'] = int(first == b.id)
        assignment = [int(j in selected) for j in range(n, model.NumVars)]
        for name, value in starts.items(): variables[variable_index[name]].Start = value
        model.setAttr('Start', variables[n:], assignment)
        model.Params.OutputFlag = 0
        model.Params.Threads = 1
        model.Params.MIPGap = model.Params.MIPGapAbs = 0
        model.Params.FeasibilityTol = model.Params.IntFeasTol = 1e-9
        model.Params.Seed = seed
        if timed:
            model.Params.Presolve = 1
            model.Params.Heuristics = 0
        # For the large OLD comparison, validating its published conservative
        # bound is sufficient. Stop when that bound is independently supported;
        # separately record that this does not reproduce an optimal status.
        replay_bound_stop = None
        if not timed and len(problem.orders) >= 50:
            replay_bound_stop = cert['bound']+min(1e-5, cert['margin']/10)+1e-6
            model.Params.BestBdStop = replay_bound_stop
        model.Params.TimeLimit = 65 if len(problem.orders) >= 50 else 10
        model.optimize()
        replay_bound, replay_status = model.ObjBound, int(model.Status)
        # Replays need to support the published, margin-reduced bound. A
        # time-limited MIP can have slightly different raw bounds after MPS
        # round-tripping and tighter tolerances. Retain an additional replay
        # allowance; never accept a discrepancy larger than the saved margin.
        replay_allowance = min(1e-5, cert['margin']/10)
        replay_supported_bound = max(reference['bound'], max(0, replay_bound-replay_allowance))
        assert replay_supported_bound >= cert['bound']-1e-6, (
            replay_supported_bound, cert['bound'], replay_bound, cert['raw_bound'], cert['margin'])
        replay_objective = float(model.ObjVal) if model.SolCount else None
        optimal_status_reproduced = model.Status == GRB.OPTIMAL
        if cert['relaxation_optimal'] and timed:
            # The new timed relaxations' optimum is a claim of this stage,
            # so require an independent optimal replay, not only a bound.
            assert optimal_status_reproduced
        if cert['relaxation_optimal'] and optimal_status_reproduced:
            close(model.ObjVal, cert['raw_bound'])
        # Fix the same FULL-M5 projection. Actual E2 departure must fit a
        # retained frontier path with no greater cost and no earlier supply cap.
        for name, value in starts.items():
            v = variables[variable_index[name]]; v.LB = v.UB = value
        model.setAttr('LB', variables[n:], assignment)
        model.setAttr('UB', variables[n:], assignment)
        model.Params.BestBdStop = -GRB.INFINITY
        model.Params.TimeLimit = 10
        model.optimize()
        assert model.Status == GRB.OPTIMAL, model.Status
        audit = audit_solution(problem, initial)
        close(model.ObjVal, audit['distances']['e1']+initial_cost)
        assert model.ObjVal <= audit['objective']+1e-6
        return dict(path=str(mps), route_choices=cert['route_choices'], complete_joint_matrix_checked=True,
            identical_E1_base_matrix=True, identical_E1_indicators=True, reoptimized_bound=replay_bound,
            raw_replay_delta=replay_bound-cert['raw_bound'], replay_allowance=replay_allowance,
            replay_supported_bound=replay_supported_bound, reported_bound_supported=True,
            reoptimized_objective=replay_objective, optimal_status_reproduced=optimal_status_reproduced,
            replay_bound_stop=replay_bound_stop,
            formal_relaxation_optimal=cert['relaxation_optimal'],
            reoptimized_status=replay_status, projected_objective=model.ObjVal,
            full_schedule_projection=True, cheapest_route_clocks_not_imposed=True,
            timing_envelopes=timed, all_supply_coefficients_checked=timed)
    finally:
        model.dispose(); base.dispose()
