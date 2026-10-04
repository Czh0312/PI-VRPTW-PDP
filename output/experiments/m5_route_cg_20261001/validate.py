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


sources = ['models/routing/solvers/exact/'+name for name in (
    'multibatch_route_cg.py', 'multibatch_route_pricing.py', 'multibatch_bounds.py', 'multibatch_cuts.py')]
sources += ['models/routing/variants/model_5/exact/'+name for name in ('bounds.py', '__init__.py')]
sources += ['tools/audit/benchmark_multibatch_route_cg.py', 'models/routing/common/cycle/precheck.py']
current = {name: sha(ROOT/name) for name in sources}
for name in sources:
    compile((ROOT/name).read_text(), str(ROOT/name), 'exec')
    target = HERE/'final_source_snapshot'/name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT/name, target)

groups, artifacts, total, column_count, witness_count = {}, {}, 0, 0, 0
for group, expected in [('probe_30s', 6), ('probe_insertion_30s', 3), ('probe_linear_30s', 3),
                        ('final_60s', 6), ('small_5s', 7)]:
    directory = HERE/group
    manifest = read(directory/'manifest.json')
    for name, digest in manifest['artifact_sha256'].items():
        assert sha(directory/name) == digest, (group, name)
    if group in ('final_60s', 'small_5s'):
        assert all(manifest['artifact_sha256']['source_snapshot/'+name] == digest for name, digest in current.items())
    rows, problems, same_incumbent = read(directory/'summary.json'), {}, defaultdict(set)
    assert len(rows) == len(manifest['jobs']) == expected
    assert {r['job']['id'] for r in rows} == {j['id'] for j in manifest['jobs']}
    metrics, cg_sites, num_columns, num_witnesses = [], 0, 0, 0
    for row in rows:
        job = row['job']
        assert row['failure'] is None
        path = directory/(job['id']+'.json')
        payload = read(path)
        assert payload['job'] == job
        if job['case'] not in problems:
            problems[job['case']] = load_instance(*load_routing_case(job['case']))
        problem = problems[job['case']]
        initial = read(Path(job['initial_result']))
        independent = audit_solution(problem, initial)
        assert independent['valid'] and payload['frozen_incumbent_audit']['valid']
        upper = independent['objective']
        close(upper, job['frozen_upper_bound'])
        same_incumbent[job['case'], job['seed']].add(sha(Path(job['initial_result'])))
        bound = payload['bound']
        assert bound is not None and math.isfinite(bound) and 0 <= bound <= upper+1e-6
        close(payload['fixed_incumbent_gap'], max(0, upper-bound)/max(1e-9, abs(upper)))
        close(row['bound'], bound)
        close(row['fixed_incumbent_gap'], payload['fixed_incumbent_gap'])
        if job['variant'] == 'milp':
            audited = audit_solution(problem, payload['result'])
            assert audited['valid'] and payload['independent_audit']['valid']
            assert row['result_valid'] and payload['result_valid']
            close(audited['objective'], payload['result']['info']['objective'])
            assert audited['objective'] <= upper+1e-6
            close(bound, payload['result']['info']['best_bound'])
            assert payload['result']['info']['distance_cut_mode'] == 'components'
        else:
            cert = payload['certificate']
            assert not cert['proven_optimal'] and not cert['proven_infeasible']
            assert cert['scope'] == 'M5_relaxed_E2_route_cover_plus_E1_certificate'
            baseline = distance_certificate(problem)
            assert cert['distance_certificate'] == baseline
            assert bound >= baseline['bound']-1e-6
            assert {s['satellite'] for s in cert['sites']} == set(problem.satellites)
            for site_index, site in enumerate(cert['sites']):
                site_id = site['satellite']
                full_trip_cost = math.fsum(trip_cost(problem, site_id, [(e['order_id'], e['kind']) for e in trip['events']])
                    for trip in initial['e2_trips'] if trip['satellite_id'] == site_id)
                assert 0 <= site['bound'] <= full_trip_cost+1e-6
                keys = {(o.id, kind) for o in problem.orders if o.satellite == site_id for kind in ('d', 'p')}
                K = min(len(keys), problem.fleet.e2_per_sat_count*problem.operating_policy.e2_max_trips_per_vehicle)
                for column in site['columns']:
                    replay_column(problem, site_id, column)
                    num_columns += 1
                candidates = []
                for witness in site['iterations']:
                    if witness.get('method') == 'insertion':
                        assert witness['candidate_bound'] is None and witness['columns_added'] > 0
                        continue
                    pi = {tuple(key): value for key, value in witness['duals']}
                    assert set(pi) == keys and witness['maximum_trips'] == K
                    nu, pricing = witness['fleet_dual'], witness['pricing']
                    assert nu <= 0
                    rho = pricing['lower_bound']
                    if pricing.get('groups'):
                        expected_categories = {None}|{o.batch for o in problem.orders if o.satellite == site_id}
                        assert {g['category'] for g in pricing['groups']} == expected_categories
                        feasible = [g for g in pricing['groups'] if not g['infeasible']]
                        close(rho, min((g['lower_bound'] for g in feasible), default=0.0))
                    if pricing['reduced_cost'] is not None:
                        assert rho <= pricing['reduced_cost']+1e-5
                        new = witness['new_column']
                        replay_column(problem, site_id, new)
                        reduced = (0 if witness['phase'] == 'feasibility' else new['cost'])-math.fsum(pi[tuple(key)] for key in new['tasks'])-nu
                        close(reduced, pricing['reduced_cost'])
                    if witness['phase'] == 'distance':
                        expected_bound = math.fsum(pi.values())+K*nu+K*min(0, rho)
                        close(witness['candidate_bound'], expected_bound)
                        candidates.append(expected_bound)
                        num_witnesses += 1
                    else:
                        assert witness['candidate_bound'] is None
                close(site['bound'], max([0.0]+candidates))
                if site.get('best_witness') is not None:
                    best = site['iterations'][site['best_witness']]
                    close(site['bound'], best['candidate_bound'])
                    exported = directory/(job['id']+'_pricing_models')
                    paths = list(exported.glob(f'site_{site_index}_best_pricing*.mps'))
                    assert paths
                    for file in paths:
                        artifacts[str(file.relative_to(HERE))] = sha(file)
                if site['lp_converged'] and site['iterations']:
                    last = site['iterations'][-1]
                    assert last.get('method', 'integer_pricing') == 'integer_pricing'
                    assert last['pricing']['raw_lower_bound'] >= -1e-6
                cg_sites += bool(site['lp_converged'])
            e1 = math.fsum(max(0, c['bound']-1e-7*max(1, c['bound'])) for c in baseline['components'] if c['echelon'] == 'E1')
            close(e1, cert['e1_bound'])
            original = {c['satellite']: max(0, c['bound']-1e-7*max(1, c['bound'])) for c in baseline['components'] if c['echelon'] == 'E2'}
            for component in cert['components']:
                close(component['distance_bound'], original.get(component['satellite'], 0))
                close(component['bound'], max(component['route_cover_bound'], component['distance_bound']))
            combined = e1+math.fsum(c['bound'] for c in cert['components'])
            close(combined, cert['raw_bound'])
            close(bound, max(baseline['bound'], max(0, combined-1e-7*max(1, combined))))
        metrics.append(dict(id=job['id'], customers=job['customers'], variant=job['variant'], upper=upper,
            bound=bound, fixed_incumbent_gap=payload['fixed_incumbent_gap'], wall_seconds=payload['wall_seconds']))
        artifacts[str(path.relative_to(HERE))] = sha(path)
        artifacts[str((directory/(job['id']+'.log')).relative_to(HERE))] = sha(directory/(job['id']+'.log'))
    assert all(len(values) == 1 for values in same_incumbent.values())
    groups[group] = dict(runs=len(rows), failures=0, incumbent_audits=len(rows), certified_bounds=len(rows),
        replayed_columns=num_columns, checked_dual_witnesses=num_witnesses, converged_cg_sites=cg_sites,
        same_initial_result=True, metrics=metrics, manifest_artifacts_verified=len(manifest['artifact_sha256']))
    total += len(rows)
    column_count += num_columns
    witness_count += num_witnesses
    for suffix in ('manifest.json', 'summary.json'):
        artifacts[str((directory/suffix).relative_to(HERE))] = sha(directory/suffix)

test_log = (HERE/'pytest.log').read_text().splitlines()[-1]
assert test_log.startswith('298 passed'), test_log
test = 'tests/routing/test_multibatch_route_cg.py'
target = HERE/'final_source_snapshot'/test
target.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(ROOT/test, target)
report = ROOT/'docs/m5_route_cg_20261001.md'
for link in re.findall(r'\]\(([^)]+)\)', report.read_text()):
    if '://' not in link and not link.endswith('validation.json'):
        assert (report.parent/link).exists(), link
result = dict(all_runs=total, formal_runs=13, exploratory_runs=12, all_incumbents_reaudited=True,
    all_milp_results_reaudited=True, all_bounds_below_audited_upper=True,
    replayed_columns=column_count, checked_dual_witnesses=witness_count,
    groups=groups, current_source_sha256=current, test_source_sha256={test: sha(ROOT/test)},
    test_log=test_log, artifact_sha256=artifacts, report_sha256=sha(report),
    validator_sha256=sha(Path(__file__)),
    note='CG certifies only a relaxation lower bound. LP convergence never proves integer M5 optimality. '
         'All pricing categories cover all pickup batches. Time-limited certificates use the corrected dual bound, '
         'not the restricted master objective. No general speedup claim from this diagnostic.')
(HERE/'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
print(json.dumps({key: result[key] for key in ('all_runs', 'formal_runs', 'exploratory_runs',
    'replayed_columns', 'checked_dual_witnesses', 'test_log')}, ensure_ascii=False))
