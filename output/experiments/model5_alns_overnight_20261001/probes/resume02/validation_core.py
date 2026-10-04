"""Frozen-source audits, independent capacity arithmetic, E1 MPS projections."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
RUNS = HERE
ROOT = HERE.parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(RUNS/'candidate_source'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_bounds import distance_certificate
from models.routing.solvers.exact.multibatch_route_pricing import route_context
from models.routing.solvers.exact.multibatch_conditional_inventory import conditional_context
import validation_capacity as capacity
from validation_e1 import verify_e1, all_patterns
import validation_columns as replay
from validation_columns import close, trip_cost


def read(path): return json.loads(path.read_text())
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def floyd(problem):
    locations = list(problem.network.nodes)
    shortest = {(a, b): problem.travel(a, b) for a in locations for b in locations}
    for mid in locations:
        for a in locations:
            for b in locations:
                shortest[a, b] = min(shortest[a, b], shortest[a, mid]+shortest[mid, b])
    replay.TRAVEL[problem] = {'outward': {a: {b: shortest[a, b] for b in locations} for a in locations}}
    return shortest


def independent_context(problem, site, shortest, condition=None):
    policy = problem.operating_policy
    release = {b.id: b.start+policy.e1_load_time+shortest[problem.depot, site]
               +problem.service(site)+policy.e2_dispatch_prep_time for b in policy.batches}
    if condition:
        bid = condition['batch']
        release[bid] = max(release[bid], condition['threshold']+policy.e2_dispatch_prep_time)
    nodes = []
    for order in problem.orders:
        if order.satellite != site: continue
        delivery = max(order.delivery_window[0], release[order.batch]+shortest[site, order.customer])
        for kind in ('d', 'p'):
            _, weight, volume = problem.task(order.id, kind)
            window = (delivery, order.delivery_window[1]) if kind == 'd' else (
                max(order.pickup_window[0], delivery+order.service_time+order.min_use_time),
                min(order.pickup_window[1], policy.batches[-1].cutoff-order.service_time
                    -shortest[order.customer, site]-policy.e2_return_unload_time))
            nodes.append(dict(key=(order.id, kind), order=order, weight=weight, volume=volume, window=window))
    return dict(satellite=site, release=release, nodes=nodes, lookup={n['key']: n for n in nodes},
                maximum_trips=min(len(nodes), problem.fleet.e2_per_sat_count*policy.e2_max_trips_per_vehicle))


def checked_audit(problem, result):
    audit = audit_solution(problem, deepcopy(result))
    assert audit['valid'] and audit['periodic_replay']['valid'], audit['errors']
    close(audit['objective'], result['info']['objective'])
    assert not result['info'].get('proven_optimal')
    return audit


def check_site(problem, row, context):
    capacity.verify_capacity_site(problem, {'site': row}, context)
    for column in row['columns']:
        for key, start in zip(column['tasks'], column['starts']):
            low, high = context['lookup'][tuple(key)]['window']
            assert low <= start <= high
        bids = {problem.order_map[o].batch for o, k in column['tasks'] if k == 'd'}
        assert column['departure'] >= max((context['release'][b] for b in bids), default=problem.operating_policy.cycle_start)


def expr(expression):
    return {expression.getVar(i).VarName: expression.getCoeff(i) for i in range(expression.size())}


def same_expr(left, right):
    left, right = expr(left), expr(right)
    assert set(left) == set(right)
    for name in left: close(left[name], right[name])


def check_joint(problem, result, reference, cert, path, old_path):
    import gurobipy as gp
    model, old = gp.read(str(path)), gp.read(str(old_path))
    try:
        patterns = all_patterns(problem)
        assert len(patterns) == cert['patterns']
        original_vars = {v.VarName: v for v in old.getVars()}
        variables = {v.VarName: v for v in model.getVars()}
        assert set(variables)-set(original_vars) == {f'conditional_E2_cost[{s}]' for s in problem.satellites}
        for name, var in original_vars.items():
            other = variables[name]
            assert var.VType == other.VType
            close(var.LB, other.LB); close(var.UB, other.UB); close(var.Obj, other.Obj)
        floor_count = sum(r['echelon'] == 'E1' for r in reference['distance_certificate']['components'])
        prefix = old.NumConstrs-floor_count
        assert model.NumConstrs == prefix+1+len(problem.satellites)+len(cert['conditional_rows'])
        for a, b in zip(old.getConstrs()[:prefix], model.getConstrs()[:prefix]):
            assert a.Sense == b.Sense
            close(a.RHS, b.RHS); same_expr(old.getRow(a), model.getRow(b))
        assert old.NumGenConstrs == model.NumGenConstrs
        for a, b in zip(old.getGenConstrs(), model.getGenConstrs()):
            av, al, ae, ass, ar = old.getGenConstrIndicator(a)
            bv, bl, be, bs, br = model.getGenConstrIndicator(b)
            assert av.VarName == bv.VarName and al == bl and ass == bs
            close(ar, br); same_expr(ae, be)
        floors = {r['satellite']: r['bound'] for r in reference['components']}
        e1_floor = model.getConstrByName('certified_E1_cost')
        assert e1_floor.Sense == '>'
        close(e1_floor.RHS, reference['e1_bound'])
        observed = expr(model.getRow(e1_floor))
        expected_cost = {v.VarName: v.Obj for v in old.getVars() if v.Obj}
        assert set(observed) == set(expected_cost)
        for name, value in expected_cost.items(): close(observed[name], value)
        for site, floor in floors.items():
            variable = variables[f'conditional_E2_cost[{site}]']
            close(variable.Obj, 1); close(variable.LB, 0)
            con = model.getConstrByName('unconditional_E2_'+site)
            close(con.RHS, floor)
            assert con.Sense == '>' and expr(model.getRow(con)) == {variable.VarName: 1}
        expected = []
        for row in cert['conditional_rows']:
            late = []
            for i, pattern in enumerate(patterns):
                if pattern['batch'] != row['batch']: continue
                clock = problem.batch_map[row['batch']].start+problem.operating_policy.e1_load_time
                previous = problem.depot
                for site in pattern['delivery']:
                    clock += problem.travel(previous, site)+problem.service(site)
                    previous = site
                    if site == row['site'] and clock >= row['threshold']: late.append(i)
            assert late == row['late_indices']
            close(row['base_bound'], floors[row['site']])
            delta = row['bound']-row['base_bound']
            coefficients = {f'conditional_E2_cost[{row["site"]}]': 1}
            coefficients.update({f'route[{i}]': -delta for i in late})
            expected.append((row['base_bound'], coefficients))
        actual = [c for c in model.getConstrs() if c.ConstrName.startswith('conditional_floor_')]
        assert len(actual) == len(expected)
        for con, (rhs, coefficients) in zip(actual, expected):
            assert con.Sense == '>'
            close(con.RHS, rhs)
            observed = expr(model.getRow(con))
            assert set(observed) == set(coefficients)
            for name, value in coefficients.items(): close(observed[name], value)
        model.Params.OutputFlag = 0; model.Params.Threads = 1; model.Params.Seed = 43
        model.Params.MIPGap = model.Params.MIPGapAbs = 0; model.Params.TimeLimit = 30
        model.optimize()
        assert model.ObjBound >= cert['raw_bound']-1e-5
        reoptimized = model.ObjBound
        def fix(name, value):
            variable = variables[name]; variable.LB = variable.UB = value
        lookup = {(r['batch'], r['delivery'], r['pickup']): i for i, r in enumerate(patterns)}
        selected, quantities = set(), {}
        for trip in result['e1_trips']:
            delivery = tuple(e['site_id'] for e in trip['events'] if e['kind'] == 'd')
            pickup = tuple(e['site_id'] for e in trip['events'] if e['kind'] == 'p')
            i = lookup[trip['batch_id'], delivery, pickup]; selected.add(i)
            quantities.update({(i, e['site_id']): e['quantity'] for e in trip['events'] if e['kind'] == 'p'})
        for i, pattern in enumerate(patterns):
            close(variables[f'route[{i}]'].Obj, pattern['cost'])
            fix(f'route[{i}]', int(i in selected))
            for site in pattern['pickup']: fix(f'quantity_{i}_{site}', quantities.get((i, site), 0))
        costs = {s: 0.0 for s in problem.satellites}
        for trip in result['e2_trips']:
            site = trip['satellite_id']
            costs[site] += trip_cost(problem, site, [(e['order_id'], e['kind']) for e in trip['events']])
            for event in trip['events']:
                oid, kind = event['order_id'], event['kind']
                fix(('delivery_' if kind == 'd' else 'pickup_time_')+oid, event['start'])
                if kind == 'p':
                    fix('e2_return_'+oid, trip['return_complete'])
                    first = next(b.id for b in problem.operating_policy.batches if trip['return_complete'] <= b.cutoff)
                    for batch in problem.operating_policy.batches: fix(f'cutoff_{oid}_{batch.id}', int(first == batch.id))
        for site, cost in costs.items(): fix(f'conditional_E2_cost[{site}]', cost)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        close(model.ObjVal, checked_audit(problem, result)['objective'])
        return dict(path=str(path), reoptimized_bound=reoptimized, full_schedule_projection=True,
                    projected_objective=model.ObjVal, patterns=len(patterns), compared_linear_rows=prefix)
    finally:
        model.dispose(); old.dispose()


