"""Frozen-source audits, independent capacity arithmetic, E1 MPS projections."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
RUNS = HERE/(sys.argv[1] if len(sys.argv) > 1 else 'fresh')
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(RUNS/'source_snapshot'))
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


def main():
    manifest = read(RUNS/'manifest.json')
    for name, digest in manifest['artifact_sha256'].items(): assert sha(RUNS/name) == digest, name
    cases, models, audits, columns = [], [], 0, 0
    summaries = read(RUNS/'summary.json')
    for summary in summaries:
        if summary['failure']:
            cases.append(dict(id=summary['job']['id'], failure=summary['failure'])); continue
        job = summary['job']; payload = read(RUNS/(job['id']+'.json'))
        p = load_instance(*load_routing_case(job['case'], use_pool_config=False))
        shortest = floyd(p); experiment = payload['experiment']; initial, result = experiment['initial_result'], experiment['result']
        before, after = checked_audit(p, initial), checked_audit(p, result); audits += 2
        assert after['objective'] <= before['objective']+1e-6
        dest = RUNS/(job['id']+'_models')
        for directory in sorted(dest.glob('primal_*/pass_*')):
            original = read(directory/'initial_result.json'); output = read(directory/'output_result.json')
            checked_audit(p, original); checked_audit(p, output); audits += 2
            assert output['info']['best_bound'] is None
            for trip, pool in zip(original['e2_trips'], read(directory/'columns.json')):
                tasks = [(e['order_id'], e['kind']) for e in trip['events']]
                assert any([tuple(k) for k in c['tasks']] == tasks for c in pool)
                for c in pool: replay.replay_column(p, trip['satellite_id'], c); columns += 1
        ref = experiment['reference_certificate']; baseline = distance_certificate(p)
        assert ref['distance_certificate'] == baseline
        models.append(verify_e1(p, result, ref['e1_certificate'], dest/'reference/e1_inventory.mps'))
        for row in ref['sites']:
            site = row['satellite']; context = independent_context(p, site, shortest)
            assert context == route_context(p, site)
            check_site(p, row, context)
            assert row['bound'] <= math.fsum(trip_cost(p, site, [(e['order_id'], e['kind']) for e in t['events']])
                for t in result['e2_trips'] if t['satellite_id'] == site)+1e-6
        for row in ref['subset_runs']:
            assert not row['restricted_objective_used_as_bound']
            check_site(p, row['certificate'], independent_context(p, row['satellite'], shortest))
        close(ref['raw_bound'], ref['e1_bound']+math.fsum(c['bound'] for c in ref['components']))
        close(ref['bound'], max(baseline['bound'], max(0, ref['raw_bound']-1e-7*max(1, abs(ref['raw_bound'])))))
        cond = experiment['conditional_certificate']
        if cond:
            for leaf in cond['conditions']:
                context = independent_context(p, leaf['site'], shortest, leaf)
                assert context == conditional_context(p, leaf['site'], leaf['batch'], leaf['threshold'])
                check_site(p, leaf['certificate'], context)
            previous = ref['bound']
            for number, master in enumerate(cond['masters']):
                close(master['bound'], max(previous, max(0, master['raw_bound']-master['margin'])))
                models.append(check_joint(p, result, ref, master, dest/f'conditional/master_{number}.mps.gz',
                                          dest/'reference/e1_inventory.mps'))
                previous = master['bound']
            close(cond['bound'], previous)
        lower = max(ref['bound'], cond['bound'] if cond else 0)
        embedded = experiment.get('adaptive_certificate')
        if embedded:
            refined = embedded['reference_certificate']
            raw = refined['e1_bound']+math.fsum(c['bound'] for c in refined['components'])
            close(refined['raw_bound'], raw)
            for row in embedded['sites']: check_site(p, row['certificate'], independent_context(p, row['satellite'], shortest))
            for row in refined['sites']: check_site(p, row, independent_context(p, row['satellite'], shortest))
            for number, record in enumerate(embedded['masters']):
                frame = deepcopy(refined)
                for component in frame['components']: component['bound'] = record['site_bounds'][component['satellite']]
                master = record['certificate']
                if master['raw_bound'] is not None:
                    models.append(check_joint(p, result, frame, master, dest/f'adaptive/master_{number}.mps.gz',
                                              dest/'reference/e1_inventory.mps'))
            for directory in sorted((dest/'adaptive').glob('primal/pass_*')):
                original = read(directory/'initial_result.json'); output = read(directory/'output_result.json')
                checked_audit(p, original); checked_audit(p, output); audits += 2
                if not (directory/'columns.json').exists():
                    assert output['info']['route_pool_status'] == 'TIME_LIMIT_BUILD'
                    close(output['info']['objective'], original['info']['objective'])
                    continue
                for trip, pool in zip(original['e2_trips'], read(directory/'columns.json')):
                    tasks = [(e['order_id'], e['kind']) for e in trip['events']]
                    assert any([tuple(k) for k in c['tasks']] == tasks for c in pool)
                    for column in pool: replay.replay_column(p, trip['satellite_id'], column); columns += 1
            lower = max(lower, refined['bound'], *(r['certificate']['bound'] for r in embedded['masters']))
            close(embedded['bound'], lower)
        close(experiment['bound'], lower); close(experiment['upper_bound'], after['objective'])
        close(experiment['gap'], max(0, after['objective']-lower)/max(1e-9, abs(after['objective'])))
        assert experiment['target_reached'] == (experiment['gap'] <= job['options']['target_gap'])
        assert payload['wall_seconds'] <= job['options']['time_limit']+.5
        cold_upper, cold_gap = after['objective'], 100*experiment['gap']
        extra_path = HERE/'adaptive'/(job['id']+'.json')
        cumulative = payload['wall_seconds']
        if extra_path.exists():
            extra = read(extra_path); adaptive = extra['experiment']
            assert sha(RUNS/(job['id']+'.json')) == extra['job']['initial_sha256']
            after = checked_audit(p, adaptive['result']); audits += 1
            assert after['objective'] <= cold_upper+1e-6
            refined = adaptive['reference_certificate']
            for row in adaptive['sites']: check_site(p, row['certificate'], independent_context(p, row['satellite'], shortest))
            for row in refined['sites']: check_site(p, row, independent_context(p, row['satellite'], shortest))
            raw = refined['e1_bound']+math.fsum(c['bound'] for c in refined['components'])
            close(refined['raw_bound'], raw)
            adest = HERE/'adaptive'/(job['id']+'_models')
            for number, record in enumerate(adaptive['masters']):
                frame = deepcopy(refined)
                for component in frame['components']: component['bound'] = record['site_bounds'][component['satellite']]
                master = record['certificate']
                if master['raw_bound'] is not None:
                    models.append(check_joint(p, adaptive['result'], frame, master, adest/f'master_{number}.mps.gz',
                                              dest/'reference/e1_inventory.mps'))
            for directory in sorted(adest.glob('primal/pass_*')):
                original = read(directory/'initial_result.json'); output = read(directory/'output_result.json')
                checked_audit(p, original); checked_audit(p, output); audits += 2
                if not (directory/'columns.json').exists():
                    assert output['info']['route_pool_status'] == 'TIME_LIMIT_BUILD'
                    close(output['info']['objective'], original['info']['objective'])
                    continue
                for trip, pool in zip(original['e2_trips'], read(directory/'columns.json')):
                    tasks = [(e['order_id'], e['kind']) for e in trip['events']]
                    assert any([tuple(k) for k in c['tasks']] == tasks for c in pool)
                    for column in pool: replay.replay_column(p, trip['satellite_id'], column); columns += 1
            lower = max(lower, refined['bound'], *(r['certificate']['bound'] for r in adaptive['masters']))
            close(adaptive['bound'], lower); close(extra['bound'], lower); close(extra['upper_bound'], after['objective'])
            close(extra['gap'], max(0, after['objective']-lower)/max(1e-9, abs(after['objective'])))
            cumulative += extra['wall_seconds']
            assert cumulative <= job['options']['time_limit']+.5
        gap = max(0, after['objective']-lower)/max(1e-9, abs(after['objective']))
        cases.append(dict(id=job['id'], family=job['family'], pool=job['pool'], initial_upper=before['objective'],
            cold_upper=cold_upper, cold_gap_percent=cold_gap, upper=after['objective'], lower=lower,
            gap_percent=100*gap, target_reached=gap <= .05, cumulative_wall_seconds=cumulative))
    record = dict(status='VERIFIED', registered_jobs=len(manifest['jobs']), completed_jobs=len(summaries),
        cases=cases, whole_cycle_audits=audits, independently_replayed_columns=columns+capacity.columns,
        repaired_dual_checks=capacity.repair_checks, mps_checks=models,
        artifact_sha256={str(f.relative_to(HERE)): sha(f)
                         for folder in (RUNS, HERE/'adaptive', HERE/'column_recovery') if folder.exists()
                         for f in folder.rglob('*') if f.is_file() and '__pycache__' not in f.parts})
    record['run_directory'] = str(RUNS)
    name = 'validation.json' if RUNS.name == 'fresh' else 'validation_'+RUNS.name+'.json'
    (HERE/name).write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: v for k, v in record.items() if k not in ('artifact_sha256', 'cases', 'mps_checks')}, ensure_ascii=False))


if __name__ == '__main__': main()
