"""Nine small M8 instances, one root seed, a shared five-second ALNS budget.

Optimal references are read only by summarize(), after all search workers exit.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CASES = [f'S{i:02}' for i in range(1, 10)]
SEED, SECONDS, TOLERANCE = 42, 5., 1e-6


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def source_hashes():
    return {p.relative_to(HERE/'source').as_posix(): sha(p) for p in (HERE/'source').rglob('*.py')}


def run():
    if (HERE/'plan.json').exists() or (HERE/'source').exists():
        raise FileExistsError('This experiment already exists; preserve the recorded run.')
    shutil.copytree(ROOT/'models', HERE/'source/models', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    inputs = {}
    for case in CASES:
        path = ROOT/f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json'
        inputs[str(path)] = sha(path)
        config = read(path)
        for key in ['data', 'routing_config', 'orders', 'operating_policy', 'economic_config',
                    'dataset_metadata', 'parameter_contract', 'batch_count_certificate']:
            if isinstance(config.get(key), str):
                extra = Path(config[key])
                extra = extra if extra.is_absolute() else ROOT/extra
                inputs[str(extra)] = sha(extra)
    plan = dict(cases=CASES, root_seeds=[SEED], seconds_per_case=SECONDS, total_search_budget=45.,
        max_iterations=10**9, concurrency=1, budget_strategy='adaptive',
        calendar_reconstruction_period=1, initial_result=None, exact_reference_used_by_search=False,
        source_hashes=source_hashes(), input_hashes=inputs, driver_sha256=sha(__file__),
        clock='Wall clock; K construction, internal starts, search, polishing and internal audits share 5s. Input reading and final independent audit are outside.',
        timing_observation='The scheduler clock is forwarded unchanged; its first perf_counter value records the exact origin of native histories.')
    save(HERE/'plan.json', plan)
    for case in CASES:
        completed = subprocess.run([sys.executable, '-X', 'utf8', str(HERE/'bench.py'), 'worker', '--case', case],
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=120,
            env={**os.environ, 'PYTHONHASHSEED': '0', **{name: '1' for name in
                 ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS']}})
        (HERE/f'{case}.log').write_text(completed.stdout+completed.stderr, encoding='utf-8')
        if completed.returncode:
            raise RuntimeError(completed.stdout+completed.stderr)
        print(completed.stdout.strip(), flush=True)
    assert source_hashes() == plan['source_hashes'] and sha(__file__) == plan['driver_sha256']
    assert all(sha(path) == digest for path, digest in inputs.items())
    summarize()


def worker(case):
    sys.path[:0] = [str(HERE/'source'), str(ROOT)]
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.model_8.alns.solver import solve_alns
    from models.routing.model_8.alns import search, fixed_calendar_search, calendar_polish
    from models.routing.model_8.alns.k_search import scheduler
    from tools.configuration.loader import load_routing_case
    p = load_instance(*load_routing_case(ROOT/f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json', use_pool_config=False))
    origins = []
    real_clock = scheduler.time
    class ObservedClock:
        @staticmethod
        def perf_counter():
            value = real_clock.perf_counter()
            if not origins:
                origins.append(value)
            return value
    scheduler.time = ObservedClock
    begun, cpu_begun = time.perf_counter(), time.process_time()
    try:
        result = solve_alns(p, max_time=SECONDS, max_iterations=10**9, seed=SEED)
    finally:
        scheduler.time = real_clock
    wall, cpu = time.perf_counter()-begun, time.process_time()-cpu_begun
    assert len(origins) == 1
    origin_offset = origins[0]-begun
    audit_begun = time.perf_counter()
    audit = audit_solution(p, result)
    audit_wall = time.perf_counter()-audit_begun
    valid = bool(audit['valid'] and audit.get('periodic_replay', {}).get('valid'))
    assert not result['info']['proven_optimal'] and result['info']['best_bound'] is None
    assert not any(name.startswith('gurobipy') or (name.startswith('models.') and '.exact' in name) for name in sys.modules)
    events = []
    for visit in result['count_search_visits']:
        if not visit['valid']:
            continue
        for event in visit.get('local_history', []):
            if isinstance(event.get('objective'), (int, float)) and math.isfinite(event['objective']):
                events.append(dict(time=origin_offset+visit['started']+event['time'],
                    objective=event['objective'], K=visit['batch_count'],
                    source='audited_local_history', operator=event.get('source')))
        if visit.get('objective') is not None:
            events.append(dict(time=origin_offset+visit['finished'], objective=visit['objective'],
                               K=visit['batch_count'], source='audited_K_return'))
    for event in result.get('anytime', []):
        if event.get('objective') is not None:
            events.append(dict(time=origin_offset+event['elapsed'], objective=event['objective'],
                               K=event['batch_count'], source='global_audited_history'))
    events.sort(key=lambda event: event['time'])
    if valid and events:
        assert audit['objective'] <= min(event['objective'] for event in events)+TOLERANCE
    save(HERE/f'runs/{case}/result.json', result)
    save(HERE/f'runs/{case}/audit.json', audit)
    save(HERE/f'runs/{case}/timeline.json', events)
    row = dict(case=case, root_seed=SEED, budget_seconds=SECONDS, wall_seconds=wall, cpu_seconds=cpu,
        audit_wall_seconds=audit_wall, native_clock_origin_offset=origin_offset,
        valid=valid, periodic_valid=bool(audit.get('periodic_replay', {}).get('valid')),
        objective=audit.get('objective') if valid else None, K=result.get('cycle_batch_count'),
        N=result.get('container_pool_size'), iterations=result['info']['iterations'],
        original_K=list(p.batch_count_candidates), exact_reference_used=False, external_initial_result_used=False,
        timing_repair_attempts=result['info'].get('timing_repair_attempts', 0),
        timing_repair_successes=result['info'].get('timing_repair_successes', 0))
    save(HERE/f'runs/{case}/summary.json', row)
    print(json.dumps(row, ensure_ascii=False), flush=True)


def summarize():
    rows, reference_hashes = [], {}
    for case in CASES:
        ref_path = ROOT/f'output/experiments/m8_all_small_rerun_20261029_v1/runs/{case}/s42/summary.json'
        ref = read(ref_path)
        assert ref['proven_optimal'] and ref['business_audit_valid'] and ref['periodic_replay_valid']
        assert abs(ref['audited_upper_bound']-ref['global_lower_bound']) <= TOLERANCE
        exact = ref['audited_upper_bound']
        reference_hashes[ref_path.relative_to(ROOT).as_posix()] = sha(ref_path)
        row = read(HERE/f'runs/{case}/summary.json')
        events = read(HERE/f'runs/{case}/timeline.json')
        before = [event for event in events if event['time'] <= SECONDS]
        best_at_5 = min((event['objective'] for event in before), default=None)
        hit_events = [event for event in before if abs(event['objective']-exact) <= TOLERANCE]
        if row['valid']:
            assert row['objective'] >= exact-TOLERANCE
            assert abs(row['objective']-exact) > TOLERANCE or hit_events or wall_return_after_limit(row)
        row.update(exact_objective=exact, gap_percent=100*(row['objective']/exact-1) if row['valid'] else None,
            objective_recorded_within_5s=best_at_5,
            gap_recorded_within_5s_percent=100*(best_at_5/exact-1) if best_at_5 is not None else None,
            known_optimum_at_return=bool(row['valid'] and abs(row['objective']-exact) <= TOLERANCE),
            known_optimum_recorded_within_5s=bool(hit_events),
            first_recorded_optimum_seconds=min((event['time'] for event in hit_events), default=None),
            historical_exact_wall_seconds=ref['total_wall_seconds'],
            historical_exact_used_feasible_plan=ref['historical_feasible_plan_used'],
            historical_exact_cold_start=ref['cold_start_end_to_end'])
        rows.append(row)
    valid = [row for row in rows if row['valid']]
    timed = [row for row in rows if row['gap_recorded_within_5s_percent'] is not None]
    summary = dict(cases=9, root_seeds=[SEED], budget_seconds=SECONDS,
        valid=len(valid), failed=9-len(valid), known_optima_at_return=sum(row['known_optimum_at_return'] for row in rows),
        known_optima_recorded_within_5s=sum(row['known_optimum_recorded_within_5s'] for row in rows),
        mean_gap_percent=statistics.mean(row['gap_percent'] for row in valid) if len(valid) == 9 else None,
        worst_gap_percent=max(row['gap_percent'] for row in valid) if len(valid) == 9 else None,
        mean_gap_recorded_within_5s_percent=statistics.mean(row['gap_recorded_within_5s_percent'] for row in timed) if len(timed) == 9 else None,
        mean_wall_seconds=statistics.mean(row['wall_seconds'] for row in rows),
        max_wall_seconds=max(row['wall_seconds'] for row in rows),
        mean_cpu_seconds=statistics.mean(row['cpu_seconds'] for row in rows),
        exact_reference_used_only_after_search=True, reference_hashes=reference_hashes, rows=rows)
    save(HERE/'comparison.json', summary)
    print(json.dumps({key: value for key, value in summary.items() if key not in ('reference_hashes', 'rows')}, ensure_ascii=False), flush=True)


def wall_return_after_limit(row):
    return row['wall_seconds'] > SECONDS


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['run', 'worker', 'summarize'])
    parser.add_argument('--case', choices=CASES)
    args = parser.parse_args()
    if args.action == 'run':
        run()
    elif args.action == 'worker':
        worker(args.case)
    else:
        summarize()
