"""Verify joint matrices and original full-M5 projections without production builders."""
from collections import Counter
import gzip
import json
import math

from models.routing.common.cycle.audit import audit_solution
from validation_e1 import all_patterns
from validation_enumeration import independent_context


def close(a, b):
    assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6), (a, b)


def indicator(model, row):
    var, value, expression, sense, rhs = model.getGenConstrIndicator(row)
    return (var.VarName, value, sorted((expression.getVar(i).VarName, expression.getCoeff(i))
            for i in range(expression.size())), sense, rhs)


def verify_joint(problem, initial, reference, cert, directory):
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
            rows = [c for group in universe['groups'] for c in group['columns']]
            taskset_index = {frozenset(tuple(k) for k in c['tasks']): i for i, c in enumerate(rows)}
            originals = {}
            for trip in initial['e2_trips']:
                if trip['satellite_id'] != site: continue
                taskset = frozenset((e['order_id'], e['kind']) for e in trip['events'])
                i = taskset_index[taskset]
                cutoff = next(b.id for b in problem.operating_policy.batches if trip['return_complete'] <= b.cutoff) \
                    if any(kind == 'p' for _, kind in taskset) else None
                originals[i, cutoff] = trip
                initial_cost += rows[i]['cost']
            for i, column in enumerate(rows):
                tasks = tuple(tuple(k) for k in column['tasks'])
                pickups = [oid for oid, kind in tasks if kind == 'p']
                lower_return = max(necessary[k] for k in tasks)
                allowed = [b.id for b in problem.operating_policy.batches if b.cutoff >= lower_return] if pickups else [None]
                assert allowed
                for batch_index, cutoff in enumerate(allowed):
                    j = next_column
                    next_column += 1
                    assert names[j] == f'joint_route_{site_index}_{i}_{batch_index}'
                    assert variables[j].VType == GRB.BINARY and variables[j].LB == 0 and variables[j].UB == 1
                    close(variables[j].Obj, column['cost'])
                    for key in tasks: add(covers[key], j, 1)
                    add(fleet, j, 1); add(site_cost, j, column['cost'])
                    for oid in pickups: add(links[oid, cutoff], j, -1)
                    mapping[site, i, cutoff] = j
                    if (i, cutoff) in originals: selected.add(j)
                    if (site, i, cutoff) in selected_rows:
                        saved = selected_rows[site, i, cutoff]
                        assert tuple(tuple(k) for k in saved['tasks']) == tasks
                        close(saved['cost'], column['cost'])
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
            counts = Counter(tuple(k) for x in columns for k in x['tasks'])
            assert counts == Counter(node['key'] for node in independent_context(problem, site)['nodes'])
        # Replay the unrestricted proof; ObjVal is never substituted for ObjBound.
        model.Params.OutputFlag = 0
        model.Params.Threads = 1
        model.Params.MIPGap = model.Params.MIPGapAbs = 0
        model.Params.FeasibilityTol = model.Params.IntFeasTol = 1e-9
        model.Params.Seed = 42
        model.Params.TimeLimit = 35 if len(problem.orders) >= 50 else 10
        model.optimize()
        assert model.ObjBound >= cert['raw_bound']-1e-5, (model.ObjBound, cert['raw_bound'])
        replay_bound, replay_status = model.ObjBound, int(model.Status)
        if cert['relaxation_optimal']:
            assert model.Status == GRB.OPTIMAL
            close(model.ObjVal, cert['raw_bound'])
        def fix(name, value):
            v = variables[variable_index[name]]
            v.LB = v.UB = value
        # Fix the audited original E1 patterns, order clocks, return cutoffs,
        # and cheapest columns with the ORIGINAL task sets and actual cutoffs.
        patterns = all_patterns(problem)
        lookup = {(p['batch'], p['delivery'], p['pickup']): i for i, p in enumerate(patterns)}
        chosen, quantities = set(), {}
        for trip in initial['e1_trips']:
            i = lookup[trip['batch_id'], tuple(e['site_id'] for e in trip['events'] if e['kind'] == 'd'),
                       tuple(e['site_id'] for e in trip['events'] if e['kind'] == 'p')]
            chosen.add(i)
            quantities.update({(i, e['site_id']): e['quantity'] for e in trip['events'] if e['kind'] == 'p'})
        for i, pattern in enumerate(patterns):
            fix(f'route[{i}]', int(i in chosen))
            for site in pattern['pickup']: fix(f'quantity_{i}_{site}', quantities.get((i, site), 0))
        for trip in initial['e2_trips']:
            for event in trip['events']:
                oid, kind = event['order_id'], event['kind']
                fix(('delivery_' if kind == 'd' else 'pickup_time_')+oid, event['start'])
                if kind == 'p':
                    fix('e2_return_'+oid, trip['return_complete'])
                    first = next(b.id for b in problem.operating_policy.batches if trip['return_complete'] <= b.cutoff)
                    for b in problem.operating_policy.batches: fix(f'cutoff_{oid}_{b.id}', int(first == b.id))
        assignment = [int(j in selected) for j in range(n, model.NumVars)]
        model.setAttr('LB', variables[n:], assignment)
        model.setAttr('UB', variables[n:], assignment)
        model.Params.TimeLimit = 10
        model.optimize()
        assert model.Status == GRB.OPTIMAL, model.Status
        audit = audit_solution(problem, initial)
        close(model.ObjVal, audit['distances']['e1']+initial_cost)
        assert model.ObjVal <= audit['objective']+1e-6
        return dict(path=str(mps), route_choices=cert['route_choices'], complete_joint_matrix_checked=True,
            identical_E1_base_matrix=True, identical_E1_indicators=True, reoptimized_bound=replay_bound,
            reoptimized_status=replay_status, projected_objective=model.ObjVal,
            full_schedule_projection=True, cheapest_route_clocks_not_imposed=True)
    finally:
        model.dispose(); base.dispose()
