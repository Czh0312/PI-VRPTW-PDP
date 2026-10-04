"""Reaudit all frozen uppers, route columns, complete domains, LPs and dual bounds."""
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
from validation_enumeration import verify_native, ORACLE_CHECKS, independent_context
from validation_integer import verify_integer
from validation_joint import verify_joint
from validation_pool import replay_pool
from validation_timing import verify_timing, verify_service, TIMING_CHECKS


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def close(a, b):
    assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6), (a, b)


def trip_cost(problem, site, tasks):
    places = [site]+[problem.order_map[key[0]].customer for key in tasks]+[site]
    return math.fsum(problem.distance(a, b) for a, b in zip(places, places[1:]))


def replay_column(problem, site, column):
    """Replay raw travel, windows, actual initial loads and integer times."""
    p, f = problem.operating_policy, problem.fleet
    tasks = [tuple(key) for key in column['tasks']]
    assert tasks and len(set(tasks)) == len(tasks) == len(column['starts'])
    context = independent_context(problem, site)
    nodes = {n['key']: n for n in context['nodes']}
    assert set(tasks) <= nodes.keys()
    batches = {problem.order_map[oid].batch for oid, kind in tasks if kind == 'd'}
    assert len(batches) <= 1
    elapsed, previous, delivered = column['departure'], site, {}
    assert isinstance(elapsed, int) and p.cycle_start <= elapsed <= p.cycle_end
    if batches:
        assert elapsed >= max(context['release'][b] for b in batches)
    load = [math.fsum(problem.task(oid, kind)[i] for oid, kind in tasks if kind == 'd') for i in (1, 2)]
    caps = [f.e2_weight_cap, f.e2_volume_cap]
    assert all(-1e-6 <= v <= c+1e-6 for v, c in zip(load, caps))
    for (oid, kind), started in zip(tasks, column['starts']):
        order, weight, volume = problem.task(oid, kind)
        assert isinstance(started, int) and started >= elapsed+problem.travel(previous, order.customer)
        n = nodes[oid, kind]
        assert n['low'] <= started <= n['high']
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




def cached(function,*args,**kwargs):
    module=Path(sys.modules[function.__module__].__file__)
    arguments=[]
    for value in args:
        if isinstance(value,Path):arguments.append((str(value),sha(value) if value.is_file() else 'directory'))
        elif isinstance(value,(dict,list,str,int,float,bool)):arguments.append(value)
        else:arguments.append(type(value).__name__)
    key=hashlib.sha256(json.dumps(dict(job=sha(path),function=function.__name__,module=sha(module),args=arguments,kwargs=kwargs),sort_keys=True).encode()).hexdigest()
    target=HERE/'validation_checkpoints'/(key+'.json')
    if target.exists():return read(target)
    result=function(*args,**kwargs)
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False)+'\n')
    return result


sources = ['models/routing/solvers/exact/'+name for name in (
    'multibatch_route_timing.py', 'multibatch_joint_inventory.py', 'multibatch_route_pool.py', 'multibatch_route_integer_cover.py', 'multibatch_route_enumeration.py', 'multibatch_route_cg.py', 'multibatch_route_pricing.py',
    'multibatch_bounds.py', 'multibatch_e1_inventory.py', 'multibatch_e1_routes.py')]
sources += ['models/routing/variants/model_5/exact/'+name for name in ('bounds.py', '__init__.py')]
sources += ['tools/audit/benchmark_multibatch_route_service.py', 'models/routing/common/cycle/precheck.py']
current = {name: sha(ROOT/name) for name in sources}
external_sources = {'models/routing/solvers/exact/multibatch_route_cg.py',
                    'models/routing/solvers/exact/multibatch_route_pricing.py'}
formal_snapshot = HERE/'verified_50_180s/source_snapshot'
for name in sources+['tests/routing/test_multibatch_joint_inventory.py', 'tests/routing/test_multibatch_route_timing.py','tests/routing/test_multibatch_route_service.py']:
    compile((ROOT/name).read_text(), str(ROOT/name), 'exec')
    target = HERE/'final_source_snapshot'/name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(formal_snapshot/name if name in sources else ROOT/name, target)

groups, artifacts, native_checks, e1_checks, integer_checks, joint_checks, recovery_checks, timing_checks = {}, {}, [], [], [], [], [], []
service_checks=[]
source_drift = {}
frozen_source_hashes = {}
total, column_count, witness_count, full_columns = 0, 0, 0, 0
for group, expected in [('verified_50_180s', 6), ('verified_medium_180s',4), ('verified_small_5s', 14), ('verified_fallback_180s', 2)]:
    directory = HERE/group
    manifest = read(directory/'manifest.json')
    for name, digest in manifest['artifact_sha256'].items():
        assert sha(directory/name) == digest, (group, name)
    frozen_source_hashes[group] = {name: manifest['artifact_sha256']['source_snapshot/'+name] for name in sources}
    for name, digest in current.items():
        frozen = frozen_source_hashes[group][name]
        if frozen != digest:
            # Concurrent work only adds an optional pricing-budget reuse path.
            # The experiment ran its unchanged frozen sources; verification
            # reconstructs its exported domains/matrices independently.
            assert name in external_sources, (group, name)
            source_drift[group+'/'+name] = dict(frozen=frozen, current_at_validation_start=digest,
                reason='Concurrent optional pricing-budget reuse; excluded from this stage patch')
    rows, problems, same_incumbent = read(directory/'summary.json'), {}, defaultdict(set)
    assert len(rows) == len(manifest['jobs']) == expected
    assert {r['job']['id'] for r in rows} == {j['id'] for j in manifest['jobs']}
    metrics, converged_sites, num_columns, num_witnesses, native_count = [], 0, 0, 0, 0
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
        assert math.isfinite(bound) and 0 <= bound <= upper+1e-6
        close(payload['fixed_incumbent_gap'], max(0, upper-bound)/max(1e-9, abs(upper)))
        close(row['bound'], bound)
        close(row['fixed_incumbent_gap'], payload['fixed_incumbent_gap'])
        experiment = payload['experiment']
        cert = experiment['reference_certificate']
        output_audit = audit_solution(problem, experiment['result'])
        assert output_audit['valid'] and output_audit['periodic_replay']['valid']
        close(output_audit['objective'], payload['upper_bound'])
        close(output_audit['objective'], experiment['upper_bound'])
        assert payload['result_valid'] and output_audit['objective'] <= upper+1e-6
        assert not experiment['proven_optimal'] and not experiment['proven_infeasible']
        close(bound, experiment['bound'])
        close(payload['gap'], max(0, output_audit['objective']-bound)/max(1e-9, abs(output_audit['objective'])))
        close(experiment['gap'], payload['gap'])
        assert payload['wall_seconds'] < job['budget']+2, payload['wall_seconds']
        assert not cert['proven_optimal'] and not cert['proven_infeasible']
        assert cert['scope'] == 'M5_relaxed_E2_route_cover_plus_E1_certificate'
        baseline = distance_certificate(problem)
        assert cert['distance_certificate'] == baseline
        assert bound >= baseline['bound']-1e-6
        assert {s['satellite'] for s in cert['sites']} == set(problem.satellites)
        exported = directory/(job['id']+'_models')
        for site_index, site in enumerate(cert['sites']):
            site_id = site['satellite']
            full_trip_cost = math.fsum(trip_cost(problem, site_id, [(e['order_id'], e['kind']) for e in trip['events']])
                for trip in initial['e2_trips'] if trip['satellite_id'] == site_id)
            assert 0 <= site['bound'] <= full_trip_cost+1e-6
            keys = {(o.id, kind) for o in problem.orders if o.satellite == site_id for kind in ('d', 'p')}
            K = min(len(keys), problem.fleet.e2_per_sat_count*problem.operating_policy.e2_max_trips_per_vehicle)
            if keys and job['variant'] == 'fallback':
                assert not site['enumeration']['complete'] and site['pricing_backend'] == 'milp'
                assert site.get('integer_cover') is None
            if keys and job['variant'] in ('timed', 'service'):
                assert site['enumeration']['complete'] and site['pricing_backend'] == 'complete_enumeration'
                universe = exported/f'site_{site_index}_best_pricing.json.gz'
                master = exported/f'site_{site_index}_best_pricing_master.mps.gz'
                native_checks.append(cached(verify_native, problem, site, universe, master, replay_column))
                full_columns += native_checks[-1]['full_columns']
                native_count += 1
                integer_master = exported/f'site_{site_index}_best_pricing_integer_master.mps.gz'
                integer_checks.append(cached(verify_integer, problem, initial, site, universe, master, integer_master, replay_column))
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
            lp_bound = max([0.0]+candidates)
            close(site.get('lp_bound', site['bound']), lp_bound)
            integer = site.get('integer_cover')
            close(site['bound'], integer['bound'] if integer else lp_bound)
            if site.get('best_witness') is not None:
                best = site['iterations'][site['best_witness']]
                close(site.get('lp_bound', site['bound']), best['candidate_bound'])
                paths = list(exported.glob(f'site_{site_index}_best_pricing*.mps*'))
                assert paths
            if site['lp_converged'] and site['iterations']:
                last = site['iterations'][-1]
                assert last.get('method', 'integer_pricing') in ('integer_pricing', 'complete_enumeration')
                assert last['pricing']['raw_lower_bound'] >= -1e-6
            converged_sites += bool(site['lp_converged'])
        e1 = math.fsum(max(0, c['bound']-1e-7*max(1, c['bound'])) for c in baseline['components'] if c['echelon'] == 'E1')
        close(e1, cert['distance_e1_bound'])
        e1_checks.append(cached(verify_e1, problem, initial, cert['e1_certificate'], exported/'e1_inventory.mps'))
        e1 = max(e1, cert['e1_certificate']['bound'])
        close(e1, cert['e1_bound'])
        original = {c['satellite']: max(0, c['bound']-1e-7*max(1, c['bound'])) for c in baseline['components'] if c['echelon'] == 'E2'}
        for component in cert['components']:
            close(component['distance_bound'], original.get(component['satellite'], 0))
            close(component['bound'], max(component['route_cover_bound'], component['distance_bound']))
        combined = e1+math.fsum(c['bound'] for c in cert['components'])
        close(combined, cert['raw_bound'])
        close(cert['bound'], max(baseline['bound'], max(0, combined-1e-7*max(1, combined))))
        if job['variant'] in ('timed', 'service'):
            joint = experiment['joint_certificate']
            assert joint is not None and bound >= cert['bound']
            if job['variant'] in ('timed','service'):
                assert joint['timing_envelopes']
                assert joint.get('service_envelopes',False) == (job['variant']=='service')
                for site_index, site in enumerate(cert['sites']):
                    if any(o.satellite == site['satellite'] for o in problem.orders):
                        timing_checks.append(cached(verify_timing, problem, site['satellite'],
                            exported/f'site_{site_index}_best_pricing_timing.json.gz', replay_column))
            else: raise AssertionError('unexpected variant')
            if job['variant']=='service':
                for site_index,site in enumerate(cert['sites']):
                    if any(o.satellite==site['satellite'] for o in problem.orders):
                        service_checks.append(cached(verify_service, problem,site['satellite'],exported/f'site_{site_index}_best_pricing_service.json.gz'))
            joint_checks.append(cached(verify_joint, problem, initial, cert, joint, exported, seed=job['seed']))
            close(bound, joint['bound'])
            assert experiment['recovery'] is not None
            if (exported/'recovery/route_pool.mps').exists():
                recovery_checks.append(cached(replay_pool, problem, exported/'recovery'))
            else:
                assert experiment['recovery']['status'] == 'TIME_LIMIT_BUILD'
                assert experiment['result'] == initial
            recovered = experiment['recovery']
            close(recovered['objective'], output_audit['objective'])
            assert recovered['improved'] == (output_audit['objective'] < upper-1e-6)
            if (exported/'recovery/route_pool.mps').exists():
                assert not experiment['result']['info']['route_pool_complete']
        else:
            assert experiment['joint_certificate'] is None and experiment['recovery'] is None
            close(bound, cert['bound'])
            close(upper, output_audit['objective'])
            if job['variant'] == 'fallback':
                assert experiment['status'] == 'PARTIAL_ENUMERATION_SEPARATE_BOUND'
        metrics.append(dict(id=job['id'], customers=job['customers'], variant=job['variant'], upper=upper,
            bound=bound, output_upper=output_audit['objective'], gap=payload['gap'], fixed_incumbent_gap=payload['fixed_incumbent_gap'], wall_seconds=payload['wall_seconds'], status=experiment['status']))
        for file in [path, directory/(job['id']+'.log'), *exported.rglob('*')]:
            if file.is_file():
                artifacts[str(file.relative_to(HERE))] = sha(file)
        (HERE/'validation_progress.json').write_text(json.dumps(dict(group=group,id=job['id'],completed_joint_checks=len(joint_checks),completed_service_checks=len(service_checks)),indent=2)+'\n')
        print('validated '+group+'/'+job['id'], flush=True)
    assert all(len(values) == 1 for values in same_incumbent.values())
    groups[group] = dict(runs=len(rows), failures=0, incumbent_audits=len(rows), certified_bounds=len(rows),
        replayed_columns=num_columns, checked_dual_witnesses=num_witnesses, converged_sites=converged_sites,
        complete_lp_replays=native_count, same_initial_result=True, metrics=metrics,
        manifest_artifacts_verified=len(manifest['artifact_sha256']))
    total += len(rows)
    column_count += num_columns
    witness_count += num_witnesses
    for suffix in ('manifest.json', 'summary.json'):
        artifacts[str((directory/suffix).relative_to(HERE))] = sha(directory/suffix)

test_log = (HERE/'pytest.log').read_text().splitlines()[-1]
test_count = re.match(r'(\d+) passed', test_log)
assert test_count and int(test_count.group(1)) >= 2000, test_log
result = dict(formal_runs=total, all_incumbents_reaudited=True, all_bounds_below_audited_upper=True,
    replayed_serialized_columns=column_count, checked_dual_witnesses=witness_count,
    complete_lp_checks=native_checks, full_lp_columns_replayed=full_columns,
    unique_independent_universe_checks=ORACLE_CHECKS, e1_checks=e1_checks, integer_cover_checks=integer_checks,
    joint_checks=joint_checks, recovery_checks=recovery_checks, timing_checks=timing_checks,
    unique_independent_timing_checks=TIMING_CHECKS, service_checks=service_checks,
    all_returned_full_schedules_reaudited=True, groups=groups, current_source_sha256=current,
    source_drift=source_drift,
    frozen_source_sha256=frozen_source_hashes, external_source_paths=sorted(external_sources),
    final_source_snapshot_is_formal_source=True,
    test_source_sha256={name: sha(ROOT/name) for name in ('tests/routing/test_multibatch_joint_inventory.py', 'tests/routing/test_multibatch_route_timing.py','tests/routing/test_multibatch_route_service.py')},
    test_log=test_log, artifact_sha256=artifacts,
    validator_sha256={name: sha(HERE/name) for name in ('validate.py', 'validation_e1.py', 'validation_enumeration.py', 'validation_integer.py', 'validation_joint.py', 'validation_pool.py', 'validation_timing.py')},
    note='All-path clocks include cost-dominated paths; compulsory intervals are necessary resource cuts. The timed model retains complete cost/latest-departure frontiers and necessary E1 supply links; physical E2 sequencing is still relaxed. All recovered upper bounds pass the full audit. Partial universes skip the joint stage. '
         'Independent prefix replay checks all enumerated task-set minimum costs; every exported full LP matrix is checked and reoptimized. '
         'No complete M5 integer optimality or infeasibility proof follows from LP convergence.')
(HERE/'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
print(json.dumps(dict(formal_runs=total, full_lp_checks=len(native_checks), full_lp_columns_replayed=full_columns,
    integer_replays=len(integer_checks), joint_replays=len(joint_checks), recovery_replays=len(recovery_checks), timing_frontiers=len(timing_checks), service_envelopes=len(service_checks), independent_universes=len(ORACLE_CHECKS), e1_replays=len(e1_checks), checked_dual_witnesses=witness_count,
    test_log=test_log), ensure_ascii=False), flush=True)
