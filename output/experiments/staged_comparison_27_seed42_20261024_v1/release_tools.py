"""Offline release verification and explicit reruns against frozen sources.

Original registered inputs, results and completion markers are never rewritten.
Use the project's approved Python launcher on the experiment computer.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
from pathlib import Path, PureWindowsPath
import sys
import tempfile
import time
import zipfile

STUDY = Path(__file__).resolve().parent
sys.path.insert(0, str(STUDY / 'sources/current'))
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    allow_nan=False) + '\n', encoding='utf-8')


def check(condition, message):
    if not condition:
        raise ValueError(message)


def close(actual, expected, label):
    check(math.isclose(actual, expected, rel_tol=1e-8, abs_tol=1e-6),
          f'{label}: {actual} != {expected}')


def local_path(name):
    return STUDY.joinpath(*PureWindowsPath(name).parts)


def verify_frozen():
    manifest = read(STUDY / 'manifest.json')
    check(sha(STUDY / 'manifest.json') == read(STUDY / 'manifest.sha256.json')['sha256'],
          'Manifest hash changed')
    count = 0
    for group in ('source_hashes', 'input_hashes'):
        for name, digest in manifest[group].items():
            check(sha(local_path(name)) == digest, f'Frozen {group}: {name}')
            count += 1
    with zipfile.ZipFile(STUDY / 'source_current.zip') as archive:
        for name in archive.namelist():
            expected = manifest['source_hashes'].get(name)
            check(expected is not None, f'Unregistered source archive entry: {name}')
            check(hashlib.sha256(archive.read(name)).hexdigest() == expected,
                  f'Source archive entry mismatch: {name}')
        check(set(archive.namelist()) == set(manifest['source_hashes']) - {'source_current.zip'},
              'Source archive does not cover the frozen source files')
    return manifest, count


def relocate(value):
    """Map recorded input references into this checkout, in memory only."""
    if isinstance(value, dict):
        return {k: relocate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [relocate(v) for v in value]
    if isinstance(value, str) and '/inputs/' in value.replace('\\', '/'):
        path = STUDY / 'inputs' / PureWindowsPath(value).name
        check(path.is_file(), f'Missing local frozen input: {value}')
        return str(path)
    return value


@contextmanager
def instance_for(job):
    # Only the temporary case has relocated paths. The frozen case bytes and
    # all physical/economic parameter values retain their registered hashes.
    with tempfile.TemporaryDirectory(prefix='staged-study-replay-') as temporary:
        case = Path(temporary) / 'case.json'
        write(case, relocate(read(local_path(job['case_path']))))
        config, fleet = load_routing_case(case, use_pool_config=False)
        yield load_instance(config, fleet), config


def verify(record_path=None):
    frozen_root = (STUDY / 'sources/current').resolve()
    for function in (load_routing_case, load_instance, audit_solution):
        module_file = Path(sys.modules[function.__module__].__file__).resolve()
        check(module_file.is_relative_to(frozen_root), f'Nonfrozen implementation imported: {module_file}')
    manifest, frozen_count = verify_frozen()
    inventory = STUDY / 'release_inventory.json'
    if inventory.exists():
        for name, digest in read(inventory)['files'].items():
            check(sha(local_path(name)) == digest, f'Release inventory hash: {name}')
    check(len(manifest['jobs']) == 117, 'Expected 117 registered jobs')
    progress = read(STUDY / 'progress.json')
    check(progress['state'] == 'COMPLETED' and progress['terminal'] == 117,
          'Experiment is not complete')
    check(not progress['execution_or_audit_errors'], 'Unresolved execution/audit errors')
    controller_exit = STUDY / 'controller_exit.json'
    if controller_exit.exists():
        check(read(controller_exit)['progress']['state'] == 'COMPLETED',
              'Controller did not finish normally')
    records, rows = [], []
    marker_count = 0
    for job in manifest['jobs']:
        attempt = STUDY / 'runs' / job['id'] / 'attempt_001'
        check(len(list(attempt.parent.glob('attempt_*'))) == 1,
              f'Unexpected additional attempt: {job["id"]}')
        marker = read(attempt / 'completion.json')
        for name, digest in marker['hashes'].items():
            check(sha(attempt / name) == digest, f'Completion hash: {job["id"]}/{name}')
            marker_count += 1
        row, result = read(attempt / 'row.json'), read(attempt / 'result.json')
        # The worker deliberately stores observed iterations in row.iterations;
        # the registered iteration limit remains in manifest.jobs[].iterations.
        check(all(row[k] == v for k, v in job.items() if k != 'iterations'),
              f'Protocol mismatch: {job["id"]}')
        check(row['iterations'] == result['info'].get('iterations'), 'Observed iterations differ')
        check(row['seed'] == 42, 'Unexpected seed')
        check(row['status'] not in ('ERROR', 'OUTER_TIMEOUT', 'INTERRUPTED', 'AUDIT_FAILED',
                                    'DIAGNOSTIC_FAILURE', 'NOT_RUN', 'RUNNING'),
              f'Nonterminal/error: {job["id"]}')
        check(row['status'] == result['info']['status'], f'Status mismatch: {job["id"]}')
        check(not row['any_audit_failure'] and not row['certificate_errors'],
              f'Diagnostic failure: {job["id"]}')
        saved = read(attempt / 'audit.json')
        check(saved['result_sha256'] == sha(attempt / 'result.json'), 'Audit/result hash mismatch')
        record = dict(id=job['id'], status=row['status'], valid=row['valid'],
                      budget_seconds=job['budget'], solve_wall_seconds=row['solve_wall_seconds'],
                      result_sha256=saved['result_sha256'], completion_sha256=sha(attempt / 'completion.json'))
        if result['info'].get('objective') is None:
            check(not row['valid'] and saved['audit']['not_applicable'], 'Invalid no-incumbent record')
            check(not row['proven_infeasible'], 'Unexpected infeasibility proof requires review')
            record['replay'] = 'NOT_APPLICABLE_NO_INCUMBENT'
        else:
            with instance_for(job) as (problem, config):
                check(len(problem.orders) == job['customer_count'], 'Customer count changed')
                check(problem.operating_policy.time_tick_seconds == 1, 'Physical clock changed')
                audit = audit_solution(problem, result, repeat=True)
                check(audit['valid'] and audit['periodic_replay']['valid']
                      and audit['periodic_replay']['cycles'] == 2, f'Replay failure: {job["id"]}')
                check(audit == saved['audit'], f'Recomputed audit differs: {job["id"]}')
                close(audit['objective'], row['objective'], 'Native objective/replay')
                check(row['valid'] and row['periodic_valid'], 'Feasibility flags differ')
                close(sum(audit['distances'].values()), row['total_distance'], 'Distance')
                for layer in ('e1', 'e2'):
                    close(audit['distances'][layer], row[layer + '_distance'], 'Layer distance')
                pool = result['container_pool_size'] if job['model'] == 8 else problem.operating_policy.container_pool_size
                if job['model'] == 8:
                    check(list(problem.batch_count_candidates) == [1, 2, 3, 4], 'M8 K domain changed')
                    selected = problem.selected_problem(result)
                    step = selected.calendar_decision_step
                    check(all(t % step == 0 for t in result['batch_boundaries']) and
                          all(t % step == 0 for t in result['batch_cutoffs'].values()), 'M8 calendar grid')
                    check(1 <= row['k'] <= 4, 'M8 chosen K out of range')
                economics = read(config['economic_config'])
                expected_rates = dict(distance_scale_to_km=1, e1_distance_cost_per_km=.1,
                    e2_distance_cost_per_km=.05, container_pool_cost_per_period=5,
                    cleaning_cost_per_return=.01, tracking_cost_per_use=0, container_wear_cost_per_use=.01,
                    e1_work_cost_per_minute=0, e2_work_cost_per_minute=0,
                    e1_vehicle_cost_per_cycle=0, e2_vehicle_cost_per_cycle=0, e1_trip_cost=0, e2_trip_cost=0)
                check(all(economics[k] == v for k, v in expected_rates.items()), 'Economic coefficients changed')
                # Independent arithmetic; do not call the driver's pricing code.
                d1, d2, q = audit['distances']['e1'], audit['distances']['e2'], problem.total
                transport = .1 * d1 + .05 * d2
                close(transport, row['transport_cost'], 'Transport cost')
                cost = read(attempt / 'cost_replay.json')
                close(transport, cost['transport'], 'Saved transport cost')
                if problem.reusable:
                    check(row['N'] == pool, 'Pool size differs')
                    close(5 * pool, row['pool_acquisition'], 'One-time purchase')
                    close(.02 * q, row['recurring_packaging'], 'Recurring packaging')
                    total = transport + 5 * pool + .02 * q
                    close(total, row['cost'], 'Total repriced cost')
                    close(total, cost['total'], 'Saved repriced cost')
                    close(sum(cost['components'].values()), total, 'Cost decomposition')
                    check(cost['components'] == row['cost_components'], 'Component record differs')
                    if job['objective_mode'] == 'cost':
                        close(total, row['objective'], 'Cost objective')
                else:
                    check(row['N'] == 0 and row['cost'] is None and cost['total'] is None,
                          'Unknown single-use price must remain symbolic')
                if job['method'] == 'exact' and row.get('best_bound') is not None:
                    check(row['best_bound'] <= row['objective'] + 1e-5, 'Exact bound contradicts incumbent')
                record.update(replay='PASS', periodic_cycles=2, transport=transport,
                              repriced_total=row['cost'], objective=row['objective'])
        rows.append(row)
        records.append(record)
    check(sum(r['valid'] for r in rows) == 109, 'Expected 109 audited feasible results')
    analysis = read(STUDY / 'analysis.json')
    check(analysis['tables']['runs'] == rows and analysis['first_attempts_only'], 'Analysis row mismatch')
    expected_counts = dict(runs=117, small_300s=9, m3_m8_cost_benefit=18,
                          distance_comparison=36, m1_m3_economic_threshold=18,
                          m1_m3_packaging_price_sensitivity=108)
    for name, expected in expected_counts.items():
        values = analysis['tables'][name]
        check(len(values) == expected, f'Table count: {name}')
        with (STUDY / 'tables' / f'{name}.csv').open(encoding='utf-8-sig', newline='') as stream:
            csv_rows = list(csv.DictReader(stream))
        check(len(csv_rows) == expected, f'CSV row count: {name}')
        for saved_row, value in zip(csv_rows, values):
            for key in saved_row:
                v = value.get(key)
                expected_cell = '' if v is None else json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
                check(saved_row[key] == expected_cell, f'CSV/analysis mismatch: {name}/{key}')
    bykey = {(r['case'], r['stage'][0], r['model'], r['method']): r for r in rows}
    for pair in analysis['tables']['m3_m8_cost_benefit']:
        a, b = (bykey[pair['case'], 'B', m, 'alns'] for m in (3, 8))
        close(pair['saving'], a['cost'] - b['cost'], 'Paired cost saving')
        close(pair['saving'], pair['transport_saving'] + pair['pool_saving'] + pair['recurring_packaging_saving'], 'Saving decomposition')
    for pair in analysis['tables']['distance_comparison']:
        a, b = (bykey[pair['case'], 'C', m, 'alns'] for m in (pair['left_model'], pair['right_model']))
        close(pair['distance_saving'], a['total_distance'] - b['total_distance'], 'Paired distance saving')
    thresholds = {}
    for threshold in analysis['tables']['m1_m3_economic_threshold']:
        a, b = (bykey[threshold['case'], 'C', m, 'alns'] for m in (1, 3))
        recurring = b['transport_cost'] - a['transport_cost'] + .02 * a['Q']
        close(threshold['first_cycle_single_use_price_threshold'], (5 * b['N'] + recurring) / a['Q'], 'First-cycle price threshold')
        close(threshold['recurring_single_use_price_threshold'], recurring / a['Q'], 'Recurring price threshold')
        thresholds[threshold['case']] = (a['Q'], 5 * b['N'], recurring)
    for scenario in analysis['tables']['m1_m3_packaging_price_sensitivity']:
        q, purchase, recurring = thresholds[scenario['case']]
        saving = q * scenario['all_in_single_use_price'] - recurring
        close(scenario['recurring_saving'], saving, 'Scenario recurring saving')
        close(scenario['first_cycle_saving'], saving - purchase, 'Scenario first-cycle saving')
        check(scenario['first_break_even_cycle'] == (max(1, math.ceil((purchase - 1e-9) / saving)) if saving > 0 else None), 'Break-even cycle')
        check(scenario['scenario_only'] and scenario['replacement_or_lifetime_not_modelled'], 'Scenario assumptions missing')
    report = dict(valid=True, checked_at=datetime.now(timezone.utc).isoformat(),
                  manifest_sha256=sha(STUDY / 'manifest.json'), frozen_files_checked=frozen_count,
                  verification_script_sha256=sha(__file__),
                  completed_artifact_hashes_checked=marker_count, planned=117, terminal=117,
                  independently_replayed_feasible=109, no_incumbent=8,
                  proven_optimal=sum(r['proven_optimal'] for r in rows),
                  status_counts=dict(Counter(r['status'] for r in rows)),
                  table_counts=expected_counts, unresolved_errors=[],
                  replay_uses_frozen_sources=True, solver_calls_during_release_verification=0,
                  records=records)
    if record_path is not None:
        write(record_path, report)
    print(json.dumps({k: v for k, v in report.items() if k != 'records'}, ensure_ascii=False))


def rerun(args):
    manifest, _ = verify_frozen()
    jobs = manifest['jobs'] if args.all else [j for j in manifest['jobs'] if j['id'] == args.job]
    check(bool(jobs), 'Unknown job id')
    target = Path(args.output).resolve()
    check(target != STUDY and not target.is_relative_to(STUDY), 'Choose a separate rerun directory')
    target.mkdir(parents=True, exist_ok=False)
    write(target / 'rerun_provenance.json', dict(source_manifest_sha256=sha(STUDY / 'manifest.json'),
          job_ids=[j['id'] for j in jobs], note='New run; never replaces original experimental evidence'))
    for job in jobs:
        with instance_for(job) as (problem, config):
            begun = time.perf_counter()
            if job['method'] == 'exact':
                module = importlib.import_module(f'models.routing.model_{job["model"]}.exact')
                result = module.solve_exact(problem, time_limit=job['budget'], seed=job['seed'],
                                            verbose=True, milp_options=dict(threads=1, formulation='expanded'))
            else:
                from models.routing.service import solve_routing
                result = solve_routing(model=job['model'], method='alns', problem=problem,
                                       alns_time=job['budget'], alns_iter=job['iterations'], seed=job['seed'])
            elapsed = time.perf_counter() - begun
            folder = target / job['id']
            folder.mkdir()
            write(folder / 'result.json', result)
            audit = audit_solution(problem, result, repeat=True) if result['info'].get('objective') is not None else dict(not_applicable=True)
            write(folder / 'audit.json', audit)
            write(folder / 'call.json', dict(job=job, solve_wall_seconds=elapsed))
            print(json.dumps(dict(job=job['id'], status=result['info']['status'], solve_wall_seconds=elapsed)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    verify_parser = sub.add_parser('verify', help='Replay all saved feasible results without invoking a solver')
    verify_parser.add_argument('--record', help='Optional destination for a new validation record')
    rerun_parser = sub.add_parser('rerun', help='Explicitly execute new calls in a separate output directory')
    group = rerun_parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--job')
    group.add_argument('--all', action='store_true')
    rerun_parser.add_argument('--output', required=True)
    args = parser.parse_args()
    verify(args.record) if args.mode == 'verify' else rerun(args)


if __name__ == '__main__':
    main()
