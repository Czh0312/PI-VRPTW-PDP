"""Run frozen static multibatch cases in isolated, wall-clock bounded processes."""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.configuration.loader import config_sources, load_routing_case, resolve_project_path
from tools.configuration.snapshots import freeze_input
from tools.configuration.container_pools import apply_pool_config
from experiments.analysis.summarize_multibatch_cycle import summarize
from experiments.analysis.summarize_static_multibatch import summarize_study
from models.common.cycle import load_instance, MultiBatchPolicy
from tools.data.static_batch_dataset import load_manifest, check_pairing, suggested_pool_sizes

INPUT_KEYS = ('data', 'orders', 'routing_config', 'operating_policy', 'algorithm_config',
              'distance_matrix', 'duration_matrix', 'dataset_metadata', 'pool_config', 'economic_config')


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def prepare_variants(args, records):
    variants = []
    for index, source in enumerate(args.case):
        cfg, fleet = load_routing_case(source, use_pool_config=False)
        # This runner explicitly scans fixed pools for historical distance
        # diagnostics. Current M5 cost optimization runs through main.py route.
        if cfg['model'] == 5:
            cfg['objective_mode'] = 'distance'
        if (fleet.get('operating_policy') or {}).get('policy_version') != 'multi_batch_cutoff_v1':
            raise ValueError(f'{source} is not a multibatch case')
        if not args.pool_sizes and not args.suggested_pools:
            cfg, fleet = apply_pool_config(cfg, fleet, args.pool_config)
        problem = load_instance(cfg, fleet)  # schema validation is separate from feasibility
        source_paths = {resolve_project_path(source).resolve()}
        source_paths.update(resolve_project_path(cfg[key]).resolve() for key in INPUT_KEYS
                            if isinstance(cfg.get(key), str))
        source_paths = {dependency for path in source_paths for dependency in config_sources(path)}
        source_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}
        record = records[index] if records else {}
        pools = (suggested_pool_sizes(record) if args.suggested_pools else
                 args.pool_sizes or [problem.operating_policy.container_pool_size])
        cleaning_values = args.cleaning_durations or [fleet['operating_policy']['cleaning_duration']]
        for ci, cleaning in enumerate(dict.fromkeys(cleaning_values)):
            scenario = f'{index:03d}_{Path(source).stem}'
            if args.cleaning_durations is not None:
                scenario += f'_clean{ci}'
            for pool in dict.fromkeys(pools):
                copied = deepcopy(cfg)
                if args.no_plots:
                    copied['save_plots'] = False
                copied['operating_policy'] = {**fleet['operating_policy'],
                                              'container_pool_size': pool, 'cleaning_duration': cleaning}
                copied['container_pool_size'] = pool
                parsed = MultiBatchPolicy.from_config(copied['operating_policy'])
                if args.iterations is not None:
                    copied['alns_iter'] = args.iterations
                variants.append(dict(case_variant=f'{scenario}_N{pool}', scenario_id=scenario, model=problem.model_number,
                                     source_case=source, case_id=record.get('case_id', Path(source).stem),
                                     family_id=record.get('family_id', ''), group=record.get('group', ''),
                                     size=record.get('size', ''), batch_count=len(parsed.batches),
                                     total_container_uses=problem.total,
                                     maximum_batch_demand=max(problem.batch_demands.values(), default=0),
                                     cleaning_duration=cleaning, pool=pool, config=copied,
                                     _source_hashes=source_hashes))
    return variants


def freeze_inputs(variants, out, dataset_manifest=None):
    """Copy every input before the first worker; all jobs read these copies."""
    expected = {}
    for variant in variants:
        for path, digest in variant['_source_hashes'].items():
            if path in expected and expected[path] != digest:
                raise ValueError(f'INPUT_CHANGED_DURING_PLANNING: {path}')
            expected[path] = digest
    originals = {Path(path) for path in expected}
    for variant in variants:
        originals.update(resolve_project_path(value).resolve() for key, value in variant['config'].items()
                         if key in INPUT_KEYS and isinstance(value, str))
    if dataset_manifest:
        originals.add(resolve_project_path(dataset_manifest).resolve())
    originals = {dependency for path in originals for dependency in config_sources(path)}
    inputs = out/'inputs'
    inputs.mkdir()
    copies, hashes = {}, {}
    for index, source in enumerate(sorted(originals)):
        payload = source.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if str(source) in expected and digest != expected[str(source)]:
            raise ValueError(f'INPUT_CHANGED_DURING_PLANNING: {source}')
        target = inputs/f'{index:04d}_{source.name}'
        records = freeze_input(source, target, out)
        item = records[str(source)]
        copies[str(source)] = str(out/item.get('resolved_snapshot', item['snapshot']))
        hashes.update(records)
    for variant in variants:
        cfg = variant['config']
        for key in INPUT_KEYS:
            if isinstance(cfg.get(key), str):
                cfg[key] = copies[str(resolve_project_path(cfg[key]).resolve())]
    return hashes


def source_snapshot(out):
    hashes = {}
    for directory in ('models', 'cli', 'experiments', 'tools'):
        for source in sorted((ROOT/directory).rglob('*.py')):
            relative = source.relative_to(ROOT)
            target = out/'source_snapshot'/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            hashes[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()
    shutil.copy2(ROOT/'main.py', out/'source_snapshot/main.py')
    hashes['main.py'] = hashlib.sha256((out/'source_snapshot/main.py').read_bytes()).hexdigest()
    return hashes


def run_worker(job, out, overhead):
    target = out/job['run']
    command = [sys.executable, str(out/'source_snapshot/main.py'), 'route',
               '--case', job['case_path'], '--solver', job['solver'], '--seed', str(job['seed']),
               '--output', str(target), '--exact-time' if job['solver'] == 'exact' else '--time', str(job['budget'])]
    begin = time.perf_counter()
    row = {key: value for key, value in job.items() if key not in ('case_path',)}
    row.update(status='ERROR', valid=False, proven_optimal=False, proven_infeasible=False,
               proof_scope='', objective=None, best_bound=None, mip_gap=None, optimal_gap_percent=None,
               minimum_pool_for_schedule=None, pool_lower_bound=None, periodic_replay=False,
               e1_distance=None, e2_distance=None, e1_working_minutes=None, e2_working_minutes=None,
               e1_vehicle_count=None, e2_vehicle_count=None, wall_time=None, error='')
    try:
        process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                 timeout=job['budget']+overhead)
        (out/f'{job["run"]}.log').write_text(process.stdout+'\n'+process.stderr, encoding='utf-8')
        path = target/'result.json'
        if process.returncode != 0:
            row['error'] = f'Worker exit code {process.returncode}; see log/error.json'
        elif path.exists():
            result = json.loads(path.read_text(encoding='utf-8'))
            info, audit = result['info'], result.get('audit', {})
            valid = bool(audit.get('valid'))
            if valid and (not isinstance(info.get('objective'), (int, float))
                          or not math.isfinite(info['objective'])):
                raise ValueError('Audited result requires a finite numeric objective')
            row.update(status=info['status'], valid=valid, proven_optimal=valid and info['proven_optimal'],
                       proven_infeasible=bool(info.get('proven_infeasible')) and not valid,
                       proof_scope=info.get('proof_scope') or '',
                       objective=info['objective'] if valid else None,
                       best_bound=info.get('best_bound'), mip_gap=info.get('mip_gap'),
                       pool_lower_bound=result.get('precheck', {}).get('pool_lower_bound'),
                       periodic_replay=valid and audit.get('periodic_replay', {}).get('valid', False),
                       minimum_pool_for_schedule=audit.get('minimum_pool_for_schedule') if valid else None,
                       error=json.dumps(audit['errors'], ensure_ascii=False) if audit.get('errors') else '')
            if valid:
                for layer in ('e1', 'e2'):
                    row[layer+'_distance'] = audit['distances'][layer]
                    row[layer+'_working_minutes'] = audit['time_metrics']['working_minutes'][layer]
                    row[layer+'_vehicle_count'] = audit[layer+'_vehicle_count']
        else:
            row['error'] = 'Worker produced no result.json'
    except subprocess.TimeoutExpired as exc:
        row.update(status='OUTER_TIMEOUT', error=f'Worker exceeded {job["budget"]+overhead:g}s')
        def decode(value):
            return value.decode(errors='replace') if isinstance(value, bytes) else value or ''
        (out/f'{job["run"]}.log').write_text(decode(exc.stdout)+'\n'+decode(exc.stderr), encoding='utf-8')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        row.update(status='ERROR', valid=False, proven_optimal=False, proven_infeasible=False,
                   periodic_replay=False, objective=None, error=f'{type(exc).__name__}: {exc}')
    row['wall_time'] = time.perf_counter()-begin
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', nargs='+')
    parser.add_argument('--dataset-manifest')
    parser.add_argument('--groups', nargs='+', choices=['single_batch', 'multi_batch'])
    parser.add_argument('--source-kinds', nargs='+', choices=['synthetic_mechanism', 'zhou_derived_scenario', 'lade_derived_scenario'])
    parser.add_argument('--sizes', nargs='+', choices=['small', 'medium', 'large'])
    parser.add_argument('--batch-counts', nargs='+', type=int)
    parser.add_argument('--families', nargs='+')
    parser.add_argument('--list-only', action='store_true', help='Validate and list selected cases; no solver/output')
    parser.add_argument('--dry-run', action='store_true', help='Validate and print every planned job; no solver/output')
    parser.add_argument('--solver', choices=['both', 'exact', 'alns'], default='both')
    parser.add_argument('--seeds', nargs='+', type=int, default=[42])
    pools = parser.add_mutually_exclusive_group()
    pools.add_argument('--pool-sizes', nargs='+', type=int)
    pools.add_argument('--pool-config', help='Override the shared demand-ratio config (used by default)')
    pools.add_argument('--suggested-pools', action='store_true', help='Use each dataset case\'s declared pool grid')
    parser.add_argument('--cleaning-durations', nargs='+', type=float, help='Separate scenarios, in input minutes')
    parser.add_argument('--time', type=float, default=2)
    parser.add_argument('--iterations', type=int)
    parser.add_argument('--no-plots', action='store_true', help='Keep schedules and audit tables without per-run PNG rendering')
    parser.add_argument('--exact-time', type=float, default=30)
    parser.add_argument('--timeout-overhead', type=float, default=30)
    parser.add_argument('--output')
    args = parser.parse_args(argv)
    if any(not math.isfinite(x) or x <= 0 for x in [args.time, args.exact_time, args.timeout_overhead]):
        parser.error('Budgets and timeout overhead must be finite and positive')
    if args.pool_sizes and any(n < 1 for n in args.pool_sizes):
        parser.error('pool sizes must be positive')
    if args.cleaning_durations and any(not math.isfinite(n) or n < 0 for n in args.cleaning_durations):
        parser.error('cleaning durations must be finite and nonnegative')
    if args.iterations is not None and args.iterations < 0:
        parser.error('iterations must be nonnegative')
    if any(n < 0 or n > 2_000_000_000 for n in args.seeds):
        parser.error('seeds must be between 0 and 2000000000')
    selected = []
    if args.dataset_manifest:
        if args.case:
            parser.error('--case and --dataset-manifest are mutually exclusive')
        _, selected = load_manifest(args.dataset_manifest, groups=args.groups, sizes=args.sizes,
                                    batch_counts=args.batch_counts, family_ids=args.families, source_kinds=args.source_kinds)
        check_pairing(selected)
        args.case = [r['case_path'] for r in selected]
    elif any((args.groups, args.sizes, args.batch_counts, args.families, args.suggested_pools, args.source_kinds)):
        parser.error('Dataset filters and suggested pools require --dataset-manifest')
    else:
        args.case = args.case or ['config/experiments/examples/multibatch_demo.json']
    variants = prepare_variants(args, selected)
    if args.list_only:
        for item in selected or [dict(case_path=p) for p in args.case]:
            print(json.dumps(item, ensure_ascii=False))
        return selected or args.case
    methods = []
    if args.solver in ('both', 'exact'):
        methods.append(('exact', args.seeds[0], args.exact_time))
    if args.solver in ('both', 'alns'):
        methods += [('alns', seed, args.time) for seed in dict.fromkeys(args.seeds)]
    jobs = []
    for variant in variants:
        for method, seed, budget in methods:
            job = {k: v for k, v in variant.items() if k != 'config' and not k.startswith('_')}
            job.update(run=f'{variant["case_variant"]}_{method}_seed{seed}', solver=method, seed=seed,
                       budget=budget, iterations=variant['config'].get('alns_iter', 5000) if method == 'alns' else None)
            jobs.append(job)
    budget = sum(job['budget'] for job in jobs)
    if args.dry_run:
        plan = dict(job_count=len(jobs), total_solver_budget=budget, jobs=jobs)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return plan
    out = (resolve_project_path(args.output) if args.output else
           ROOT/'output/experiments/multi_batch_cutoff_v1'/datetime.now().strftime('%Y%m%dT%H%M%S_%f')).resolve()
    if out.exists() and any(out.iterdir()):
        parser.error(f'Output directory is not empty: {out}')
    out.mkdir(parents=True, exist_ok=True)
    inputs = freeze_inputs(variants, out, args.dataset_manifest)
    sources = source_snapshot(out)
    (out/'generated_cases').mkdir()
    for variant in variants:
        case = out/'generated_cases'/f'{variant["case_variant"]}.json'
        write_json(case, variant['config'])
    for job in jobs:
        job['case_path'] = str(out/'generated_cases'/f'{job["case_variant"]}.json')
    write_json(out/'manifest.json', dict(study_version='static_multibatch_study_v1', arguments=vars(args),
        python=sys.version, selected_dataset_cases=selected, input_snapshots=inputs, source_sha256=sources,
        jobs=jobs, job_count=len(jobs), total_solver_budget=budget,
        information_mode='static_all_known', assignment_mode='fixed_input'))
    print(f'{len(jobs)} jobs; total solver budget {budget:g}s; {out}', flush=True)
    rows = []
    for job in jobs:
        row = run_worker(job, out, args.timeout_overhead)
        rows.append(row)
        with (out/'runs.csv').open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerows(rows)
        print(f"{job['run']}: {row['status']} valid={row['valid']}", flush=True)
    summarize(out)
    summarize_study(out)
    return out


if __name__ == '__main__':
    main()
