"""Reaudit incumbents and M5 solutions; replay partial trips and bound arithmetic."""
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_bounds import distance_certificate
from validation_e1 import verify_e1
E1_CHECKS = []


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def close(a, b):
    assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6), (a, b)


def trip_cost(problem, site, tasks):
    places = [site]+[problem.order_map[key[0]].customer for key in tasks]+[site]
    return math.fsum(problem.distance(a, b) for a, b in zip(places, places[1:]))


TRAVEL = {}


def replay_column(problem, site, column):
    """Replay serialized times and loads; do not call the pricing evaluator."""
    p, f = problem.operating_policy, problem.fleet
    tasks = [tuple(key) for key in column['tasks']]
    assert tasks and len(set(tasks)) == len(tasks) == len(column['starts'])
    assert all(problem.order_map[oid].satellite == site and kind in ('d', 'p') for oid, kind in tasks)
    batches = {problem.order_map[oid].batch for oid, kind in tasks if kind == 'd'}
    assert len(batches) <= 1
    elapsed, previous, delivered = column['departure'], site, {}
    assert isinstance(elapsed, int) and p.cycle_start <= elapsed <= p.cycle_end
    # Necessary supply time: shortest travel can only relax actual E1 supply.
    if batches:
        from models.routing.common.cycle.precheck import travel_lower_bounds
        if problem not in TRAVEL:
            TRAVEL[problem] = travel_lower_bounds(problem)
        lower = TRAVEL[problem]['outward'][problem.depot][site]
        assert elapsed >= max(problem.batch_map[b].start+p.e1_load_time+lower+problem.service(site)
                              +p.e2_dispatch_prep_time for b in batches)
    load = [math.fsum(problem.task(oid, kind)[i] for oid, kind in tasks if kind == 'd') for i in (1, 2)]
    caps = [f.e2_weight_cap, f.e2_volume_cap]
    assert all(-1e-6 <= v <= c+1e-6 for v, c in zip(load, caps))
    for (oid, kind), started in zip(tasks, column['starts']):
        order, weight, volume = problem.task(oid, kind)
        assert isinstance(started, int) and started >= elapsed+problem.travel(previous, order.customer)
        low, high = order.delivery_window if kind == 'd' else order.pickup_window
        assert low <= started <= high
        if kind == 'p' and (oid, 'd') in tasks:
            assert oid in delivered and started >= delivered[oid]+order.service_time+order.min_use_time
        if kind == 'd':
            delivered[oid] = started
        for i, amount in enumerate((weight, volume)):
            load[i] += amount*(1 if kind == 'p' else -1)
        assert all(-1e-6 <= v <= c+1e-6 for v, c in zip(load, caps))
        elapsed, previous = started+order.service_time, order.customer
    returned = column['return_complete']
    assert isinstance(returned, int) and returned >= elapsed+problem.travel(previous, site)+p.e2_return_unload_time
    assert returned+p.e2_turnaround_time <= p.cycle_end
    if any(kind == 'p' for _, kind in tasks):
        assert returned <= p.batches[-1].cutoff
    close(column['cost'], trip_cost(problem, site, tasks))


from validation_pool import replay_pool

sources = ['models/routing/solvers/exact/'+name for name in (
    'multibatch_route_pool.py', 'multibatch_e1_polish.py', 'multibatch_e1_routes.py', 'multibatch_e1_inventory.py',
    'multibatch_route_cg.py', 'multibatch_route_pricing.py', 'multibatch_bounds.py')]
sources += ['models/routing/variants/model_5/exact/'+name for name in ('solver.py', 'primal.py', '__init__.py')]
sources += ['tools/audit/benchmark_multibatch_route_pool.py']
current = {name: sha(ROOT/name) for name in sources}
for name in sources:
    compile((ROOT/name).read_text(), str(ROOT/name), 'exec')
    target = HERE/'final_source_snapshot'/name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT/name, target)

groups, artifacts, column_count, witness_count, total, polish_checks = {}, {}, 0, 0, 0, []
for group, count in [('final_standalone_10s', 18), ('final_small_5s', 14), ('final_cg_60s', 6), ('final_milp_60s', 2)]:
    directory = HERE/group
    manifest, rows = read(directory/'manifest.json'), read(directory/'summary.json')
    for name, digest in manifest['artifact_sha256'].items():
        assert sha(directory/name) == digest, (group, name)
    assert all(manifest['artifact_sha256']['source_snapshot/'+name] == digest for name, digest in current.items())
    assert len(rows) == len(manifest['jobs']) == count
    assert {r['job']['id'] for r in rows} == {j['id'] for j in manifest['jobs']}
    problems, initial_hashes, metrics = {}, defaultdict(set), []
    for row in rows:
        assert row['failure'] is None
        job = row['job']
        payload = read(directory/(job['id']+'.json'))
        assert payload['job'] == job
        if job['case'] not in problems:
            problems[job['case']] = load_instance(*load_routing_case(job['case']))
        p = problems[job['case']]
        initial = read(Path(job['initial_result']))
        before, audited = audit_solution(p, initial), audit_solution(p, payload['result'])
        assert before['valid'] and audited['valid'] and audited['periodic_replay']['valid']
        close(before['objective'], job['initial_upper'])
        upper = audited['objective']
        close(upper, row['upper'])
        close(upper, payload['result']['info']['objective'])
        assert upper <= before['objective']+1e-6
        initial_hashes[job['case'], job['seed']].add(sha(Path(job['initial_result'])))
        variant = job['variant']
        if group == 'final_small_5s':
            assert initial['info']['proven_optimal']
            close(upper, before['objective'])
        if variant not in ('milp', 'pool-milp'):
            initial_slots = {(t['satellite_id'], t['vehicle_id'], t['trip_index']) for t in initial['e2_trips']}
            output_slots = {(t['satellite_id'], t['vehicle_id'], t['trip_index']) for t in payload['result']['e2_trips']}
            assert output_slots <= initial_slots
            if variant == 'cg':
                assert payload['result']['e2_trips'] == initial['e2_trips']
        if variant in ('reorder', 'pool', 'pool-cg'):
            info = payload['result']['info']
            assert not info['proven_optimal'] and not info['proven_infeasible']
            assert not info['route_pool_complete']
            assert info['best_bound'] is None and info['mip_gap'] is None
            chain = initial
            for trace in info['route_pool_passes']:
                folder = directory/(job['id']+'_pool_models')/('pass_'+str(trace['pass_index']))
                step_input = read(folder/'initial_result.json')
                step_output = read(folder/'output_result.json')
                assert step_input['e1_trips'] == chain['e1_trips'] and step_input['e2_trips'] == chain['e2_trips']
                close(audit_solution(p, step_input)['objective'], trace['upper_before'])
                close(audit_solution(p, step_output)['objective'], trace['upper_after'])
                assert trace['upper_after'] <= trace['upper_before']+1e-6
                columns = read(folder/'columns.json')
                assert len(columns) == len(step_input['e2_trips'])
                for trip, pool in zip(step_input['e2_trips'], columns):
                    original = [(e['order_id'], e['kind']) for e in trip['events']]
                    assert [tuple(k) for k in pool[0]['tasks']] == original
                    assert len(pool) <= 128
                    for column in pool:
                        replay_column(p, trip['satellite_id'], column)
                        column_count += 1
                        if variant == 'reorder':
                            assert set(map(tuple, column['tasks'])) == set(original)
                polish_checks.append(replay_pool(p, folder))
                for file in folder.iterdir():
                    if file.is_file():
                        artifacts[str(file.relative_to(HERE))] = sha(file)
                chain = step_output
            assert chain['e1_trips'] == payload['result']['e1_trips'] and chain['e2_trips'] == payload['result']['e2_trips']
        bound = payload['bound']
        if variant in ('reorder', 'pool'):
            assert bound is None and payload['gap'] is None
        else:
            assert math.isfinite(bound) and 0 <= bound <= upper+1e-6
            close(payload['gap'], max(0, upper-bound)/max(1e-9, abs(upper)))
            close(payload['fixed_initial_gap'], max(0, before['objective']-bound)/max(1e-9, abs(before['objective'])))
            close(row['bound'], bound)
        if variant in ('cg', 'pool-cg'):
            cert = payload['certificate']
            assert not cert['proven_optimal'] and not cert['proven_infeasible']
            baseline = distance_certificate(p)
            assert cert['distance_certificate'] == baseline
            distance_e1 = math.fsum(max(0, r['bound']-1e-7*max(1, r['bound'])) for r in baseline['components'] if r['echelon'] == 'E1')
            close(distance_e1, cert['distance_e1_bound'])
            e1 = cert['e1_certificate']
            e1_path = directory/(job['id']+'_pricing_models/e1_inventory.mps')
            E1_CHECKS.append(verify_e1(p, payload['result'], e1, e1_path))
            artifacts[str(e1_path.relative_to(HERE))] = sha(e1_path)
            close(cert['e1_bound'], max(distance_e1, e1['bound']))
            for index, site in enumerate(cert['sites']):
                site_upper = math.fsum(trip_cost(p, site['satellite'], [(e['order_id'], e['kind']) for e in t['events']])
                    for t in payload['result']['e2_trips'] if t['satellite_id'] == site['satellite'])
                assert site['bound'] <= site_upper+1e-6
                K = min(2*len([o for o in p.orders if o.satellite == site['satellite']]),
                        p.fleet.e2_per_sat_count*p.operating_policy.e2_max_trips_per_vehicle)
                candidates = [0.0]
                for column in site['columns']:
                    replay_column(p, site['satellite'], column)
                    column_count += 1
                for w in site['iterations']:
                    if w.get('method') == 'insertion':
                        assert w['candidate_bound'] is None
                        continue
                    pi = {tuple(key): value for key, value in w['duals']}
                    assert set(pi) == {(o.id, kind) for o in p.orders if o.satellite == site['satellite'] for kind in ('d', 'p')}
                    price, nu = w['pricing'], w['fleet_dual']
                    assert nu <= 0 and w['maximum_trips'] == K
                    groups_priced = [g for g in price['groups'] if not g['infeasible']]
                    close(price['lower_bound'], min((g['lower_bound'] for g in groups_priced), default=0))
                    assert {g['category'] for g in price['groups']} == {None}|{o.batch for o in p.orders if o.satellite == site['satellite']}
                    if price['reduced_cost'] is not None:
                        column = w['new_column']
                        replay_column(p, site['satellite'], column)
                        close(price['reduced_cost'], (0 if w['phase'] == 'feasibility' else column['cost'])-math.fsum(pi[tuple(k)] for k in column['tasks'])-nu)
                        assert price['lower_bound'] <= price['reduced_cost']+1e-5
                    if w['phase'] == 'distance':
                        value = math.fsum(pi.values())+K*nu+K*min(0, price['lower_bound'])
                        close(value, w['candidate_bound'])
                        candidates.append(value)
                        witness_count += 1
                close(site['bound'], max(candidates))
                if site.get('best_witness') is not None:
                    paths_exported = list((directory/(job['id']+'_pricing_models')).glob(f'site_{index}_best_pricing*.mps'))
                    assert paths_exported
                    for file in paths_exported:
                        artifacts[str(file.relative_to(HERE))] = sha(file)
            for component in cert['components']:
                old = next(r for r in baseline['components'] if r['echelon'] == 'E2' and r['satellite'] == component['satellite'])
                close(component['distance_bound'], max(0, old['bound']-1e-7*max(1, old['bound'])))
                close(component['bound'], max(component['distance_bound'], component['route_cover_bound']))
            raw = cert['e1_bound']+math.fsum(c['bound'] for c in cert['components'])
            close(raw, cert['raw_bound'])
            close(bound, max(baseline['bound'], max(0, raw-1e-7*max(1, raw))))
        elif variant in ('milp', 'pool-milp'):
            close(bound, payload['result']['info']['best_bound'])
            if variant == 'pool-milp':
                assert not payload['result']['info']['route_pool']['route_pool_complete']
                assert payload['result']['info']['route_pool']['objective'] >= upper-1e-6
        metrics.append(dict(id=job['id'], upper_before=before['objective'], upper_after=upper,
                            bound=bound, gap=payload['gap'], wall_seconds=payload['wall_seconds']))
        for suffix in ('.json', '.log'):
            file = directory/(job['id']+suffix)
            artifacts[str(file.relative_to(HERE))] = sha(file)
    assert all(len(hashes) == 1 for hashes in initial_hashes.values())
    groups[group] = dict(runs=len(rows), failures=0, full_audits=len(rows), same_initial=True, metrics=metrics)
    total += len(rows)
    for name in ('manifest.json', 'summary.json'):
        file = directory/name
        artifacts[str(file.relative_to(HERE))] = sha(file)

test_log = (HERE/'pytest.log').read_text().splitlines()[-1]
assert test_log.startswith('424 passed')
test = 'tests/routing/test_multibatch_route_pool.py'
target = HERE/'final_source_snapshot'/test
target.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(ROOT/test, target)
report = ROOT/'docs/m5_route_pool_20261001.md'
for link in re.findall(r'\]\(([^)]+)\)', report.read_text()):
    if '://' not in link and not link.endswith('validation.json'):
        assert (report.parent/link).exists(), link
out = dict(runs=total, all_incumbents_and_outputs_reaudited=True, all_bounds_below_audited_upper=True,
    all_E2_tasks_and_physical_slots_valid=True, rebuilt_route_pool_models=polish_checks, e1_certificate_models=E1_CHECKS,
    replayed_E2_columns=column_count, distance_dual_witnesses=witness_count, groups=groups,
    source_sha256=current, test_source_sha256=sha(ROOT/test), test_log=test_log,
    artifact_sha256=artifacts, report_sha256=sha(report), validator_sha256=sha(Path(__file__)),
    pool_validator_sha256=sha(HERE/'validation_pool.py'), e1_validator_sha256=sha(HERE/'validation_e1.py'),
    note='Restricted route-pool optimality/bounds never prove full M5. CG and MILP comparisons include polishing in their original budgets.')
(HERE/'validation.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
print(json.dumps(dict(runs=total, rebuilt_route_pool_models=len(polish_checks), e1_models=len(E1_CHECKS),
    columns=column_count, dual_witnesses=witness_count, test_log=test_log)))
