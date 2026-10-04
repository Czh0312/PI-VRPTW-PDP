"""Frozen M5 B2/B3 exact and ALNS benchmark, with durable progress and resume."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from configuration.loader import load_routing_case, resolve_project_path
from experiments.runs.run_multibatch_cycle import INPUT_KEYS, freeze_inputs, source_snapshot, write_json
from experiments.runs.run_single_batch_benchmark import comparisons, execute, fmt, mean, now, save_csv
from models.routing.common.cycle import load_instance
from tools.data.static_batch_dataset import load_manifest, check_pairing


def plan(args):
    _, records = load_manifest(args.dataset_manifest, batch_counts=args.batch_counts,
                              sizes=args.sizes, family_ids=args.families,
                              source_kinds=['zhou_derived_scenario'])
    check_pairing(records)
    records.sort(key=lambda r: (r['customer_count'], r['case_id']))
    if not records:
        raise ValueError('No selected M5 cases')
    variants, jobs = [], []
    for record in records:
        config, fleet = load_routing_case(record['case_path'])
        problem = load_instance(config, fleet)
        if problem.model_number != 5 or len(problem.operating_policy.batches) < 2:
            raise ValueError('Not an M5 multibatch case: '+record['case_id'])
        paths = {resolve_project_path(record['case_path']).resolve()}
        paths.update(resolve_project_path(config[key]).resolve() for key in INPUT_KEYS
                     if isinstance(config.get(key), str))
        hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        copied = deepcopy(config)
        copied.update(model=5, alns_iter=args.iterations, save_plots=False)
        # Explicitly freeze the currently resolved shared pool setting.
        copied['container_pool_size'] = problem.operating_policy.container_pool_size
        name = record['case_id']+'_M5'
        variants.append(dict(config=copied, _source_hashes=hashes, name=name))
        methods = [('exact', args.seeds[0], args.exact_time)]
        methods += [('alns', seed, args.time) for seed in dict.fromkeys(args.seeds)]
        for solver, seed, budget in methods:
            jobs.append(dict(job_index=len(jobs), run=f'{name}_{solver}_seed{seed}',
                             case_id=record['case_id'], family_id=record['family_id'],
                             size=record['size'], customer_count=record['customer_count'],
                             batch_count=record['batch_count'],
                             pool=problem.operating_policy.container_pool_size,
                             model=5, solver=solver, seed=seed, budget=budget, variant_name=name))
    return records, variants, jobs


def save_progress(out, manifest, rows, status='RUNNING'):
    rows = sorted(rows, key=lambda r: r['job_index'])
    save_csv(out/'runs.csv', rows)
    compared = comparisons(manifest['jobs'], rows)
    metadata = {j['case_id']: j for j in manifest['jobs']}
    for item in compared:
        item.update(batch_count=metadata[item['case_id']]['batch_count'],
                    pool=metadata[item['case_id']]['pool'])
    save_csv(out/'comparison.csv', compared)
    errors = [r for r in compared if r['consistency_errors']]
    import os
    state = dict(status=status, pid=os.getpid(), started_at=manifest['started_at'],
                 updated_at=now(), completed=len(rows), total=len(manifest['jobs']),
                 valid=sum(r['valid'] and r['periodic_replay'] for r in rows),
                 outcomes=dict(Counter(r['status'] for r in rows)), consistency_errors=errors,
                 after_progress=manifest['arguments'].get('after_progress'))
    temporary = out/'progress.json.tmp'
    write_json(temporary, state)
    temporary.replace(out/'progress.json')
    args = manifest['arguments']
    lines = ['# M5 精确算法与 ALNS 全规模对比', '',
             f"状态：{status}；进度：{len(rows)}/{len(manifest['jobs'])}。",
             f"数据：{len(manifest['selected_dataset_cases'])} 个 B2/B3 算例；固定分仓、当前共享箱池配置。",
             f"精确预算 {args['exact_time']:g} 秒/例；ALNS {args['time']:g} 秒/种子；种子 {args['seeds']}。",
             f"并发 {args['workers']}；精确求解器每次 1 线程；外层超时余量 {args['timeout_overhead']:g} 秒。", '',
             '精确算法使用完整联合 MILP 入口；ALNS 独立从头运行，不调用 hybrid、不共享历史上下界或其他种子初解。',
             '保持订单、固定分仓、车队、批次、清洗、箱池与周期规则；全部失败和超时留存，不自动重试。',
             '相对精确解差距可为负；只有已证最优的精确解才作为实际最优误差的参照。',
             '认证界差为 (ALNS距离−有效下界)/ALNS距离；未知最优时不能称为实际最优误差。',
             '精确与 ALNS 预算不同，不作同预算速度优劣结论。三种子与同源情景不是独立几何样本。', '',
             '| 规模 | 批次 | 精确完成/计划 | 精确可行 | 已证最优 | ALNS完成/计划 | ALNS可行 | 最好相对精确 % | 均值相对精确 % | 配对例数 |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    aggregates = []
    for size in ('small', 'medium', 'large'):
        for batch in sorted({j['batch_count'] for j in manifest['jobs']}):
            planned = [j for j in manifest['jobs'] if j['size']==size and j['batch_count']==batch]
            if not planned:
                continue
            subset = [r for r in rows if r['size']==size and r['batch_count']==batch]
            exact = [r for r in subset if r['solver']=='exact']
            alns = [r for r in subset if r['solver']=='alns']
            pairs = [r for r in compared if r['size']==size and r['batch_count']==batch
                     and r['completed_runs']==r['planned_runs'] and not r['consistency_errors']]
            paired = [r for r in pairs if r['alns_best_vs_exact_percent'] is not None]
            item = dict(size=size, batch_count=batch, model=5, cases=len(planned)//(1+len(set(args['seeds']))),
                        exact_completed=len(exact), exact_planned=sum(j['solver']=='exact' for j in planned),
                        exact_valid=sum(r['valid'] and r['periodic_replay'] for r in exact),
                        exact_optimal=sum(r['proven_optimal'] and r['periodic_replay'] for r in exact),
                        exact_mean_solver_seconds=mean([r.get('solver_runtime') for r in exact]),
                        alns_completed=len(alns), alns_planned=sum(j['solver']=='alns' for j in planned),
                        alns_valid=sum(r['valid'] and r['periodic_replay'] for r in alns),
                        alns_mean_solver_seconds=mean([r.get('solver_runtime') for r in alns]),
                        paired_cases=len(paired),
                        mean_best_vs_exact_percent=mean([r['alns_best_vs_exact_percent'] for r in paired]),
                        mean_mean_vs_exact_percent=mean([r['alns_mean_vs_exact_percent'] for r in paired]),
                        proven_optimum_pairs=sum(r['alns_best_optimality_gap_percent'] is not None for r in pairs),
                        mean_best_optimality_gap_percent=mean([r['alns_best_optimality_gap_percent'] for r in pairs]),
                        mean_best_bound_gap_percent=mean([r['alns_best_bound_gap_percent'] for r in pairs]))
            aggregates.append(item)
            lines.append(f"| {size} | {batch} | {len(exact)}/{item['exact_planned']} | {item['exact_valid']} | "
                         f"{item['exact_optimal']} | {len(alns)}/{item['alns_planned']} | {item['alns_valid']} | "
                         f"{fmt(item['mean_best_vs_exact_percent'])} | {fmt(item['mean_mean_vs_exact_percent'])} | {len(paired)} |")
    save_csv(out/'summary.csv', aggregates)
    lines += ['', '逐次结果：[runs.csv](runs.csv)；逐例对比：[comparison.csv](comparison.csv)；'
              '规模汇总：[summary.csv](summary.csv)；冻结登记：[manifest.json](manifest.json)。', '',
              f'上下界或最优声明矛盾：{len(errors)} 项。', '',
              '| 顾客数 | B | 案例 | 箱池 | 精确状态 | 精确距离 | 下界 | ALNS可行/完成/计划 | 最好距离 | 均值距离 | 最好相对精确 % | 最好实际最优误差 % |',
              '| ---: | ---: | --- | ---: | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |']
    for r in compared:
        lines.append(f"| {r['customer_count']} | {r['batch_count']} | {r['case_id']} | {r['pool']} | "
                     f"{r['exact_status']} | {fmt(r['exact_objective'])} | {fmt(r['exact_best_bound'])} | "
                     f"{r['alns_valid']}/{r['alns_completed']}/{r['alns_planned']} | {fmt(r['alns_best'])} | "
                     f"{fmt(r['alns_mean'])} | {fmt(r['alns_best_vs_exact_percent'])} | {fmt(r['alns_best_optimality_gap_percent'])} |")
    temporary = out/'report.md.tmp'
    temporary.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    temporary.replace(out/'report.md')
    if status=='COMPLETED':
        write_json(out/'verification.json', dict(executed_runs=len(rows), planned_runs=len(manifest['jobs']),
            all_jobs_recorded={r['run'] for r in rows}=={j['run'] for j in manifest['jobs']},
            all_valid_results_periodic=all(r['periodic_replay'] for r in rows if r['valid']),
            consistency_errors=errors, result_sha256={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest()
                                                     for p in out.glob('*/result.json')}))


def verify_frozen(out, manifest):
    for relative, digest in manifest['source_sha256'].items():
        if hashlib.sha256((out/'source_snapshot'/relative).read_bytes()).hexdigest()!=digest:
            raise ValueError('Frozen source changed: '+relative)
    for entry in manifest['input_snapshots'].values():
        if hashlib.sha256((out/entry['snapshot']).read_bytes()).hexdigest()!=entry['sha256']:
            raise ValueError('Frozen input changed: '+entry['snapshot'])
    for relative, digest in manifest['generated_case_sha256'].items():
        if hashlib.sha256((out/relative).read_bytes()).hexdigest()!=digest:
            raise ValueError('Frozen generated case changed: '+relative)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-manifest', default='data/manifest.json')
    parser.add_argument('--sizes', nargs='+', choices=['small','medium','large'])
    parser.add_argument('--families', nargs='+')
    parser.add_argument('--batch-counts', nargs='+', type=int, choices=[2,3], default=[2,3])
    parser.add_argument('--seeds', nargs='+', type=int, default=[42,43,44])
    parser.add_argument('--exact-time', type=float, default=300)
    parser.add_argument('--time', type=float, default=30)
    parser.add_argument('--iterations', type=int, default=1000000)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--timeout-overhead', type=float, default=60)
    parser.add_argument('--output', required=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--after-progress', help='Wait for this prior benchmark progress.json to be COMPLETED')
    args = parser.parse_args(argv)
    if any(not math.isfinite(v) or v<=0 for v in (args.exact_time,args.time,args.timeout_overhead)):
        parser.error('Budgets must be finite and positive')
    if args.workers<1 or args.iterations<0 or any(s<0 or s>2_000_000_000 for s in args.seeds):
        parser.error('Invalid workers, iterations or seeds')
    out = resolve_project_path(args.output).resolve()
    rows = []
    if args.resume:
        manifest = json.loads((out/'manifest.json').read_text(encoding='utf-8'))
        verify_frozen(out, manifest)
        rows = [json.loads(p.read_text(encoding='utf-8')) for p in out.glob('*.row.json')]
        names = {j['run'] for j in manifest['jobs']}
        if any(r['run'] not in names for r in rows) or len({r['run'] for r in rows})!=len(rows):
            raise ValueError('Unexpected or duplicate saved rows')
    else:
        records, variants, jobs = plan(args)
        if args.dry_run:
            print(json.dumps(dict(case_count=len(records), job_count=len(jobs),
                  groups=dict(Counter(f"{r['size']}_B{r['batch_count']}" for r in records)),
                  solver_budget_seconds=sum(j['budget'] for j in jobs)), ensure_ascii=False, indent=2))
            return
        if out.exists() and any(out.iterdir()):
            parser.error('Refusing to overwrite nonempty output: '+str(out))
        out.mkdir(parents=True, exist_ok=True)
        inputs = freeze_inputs(variants, out, args.dataset_manifest)
        sources = source_snapshot(out)
        (out/'generated_cases').mkdir()
        for variant in variants:
            write_json(out/'generated_cases'/(variant['name']+'.json'),variant['config'])
        for job in jobs:
            job['case_path'] = str(out/'generated_cases'/(job['variant_name']+'.json'))
        manifest = dict(study_version='model5_exact_alns_comparison_v1', started_at=now(),
                        arguments=vars(args), python=sys.version, selected_dataset_cases=records,
                        input_snapshots=inputs, source_sha256=sources, jobs=jobs,
                        generated_case_sha256={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest()
                                               for p in out.glob('generated_cases/*.json')},
                        execution='frozen subprocesses; independent audit and two-cycle replay; no retries',
                        algorithm_changes=False, threads_per_solver=1,
                        solver_budget_seconds=sum(j['budget'] for j in jobs))
        write_json(out/'manifest.json', manifest)
    if args.prepare_only:
        save_progress(out, manifest, rows, status='PREPARED')
        print(f"Prepared {len(manifest['jobs'])} jobs: {out}", flush=True)
        return out
    prerequisite = manifest['arguments'].get('after_progress')
    if prerequisite:
        prerequisite = resolve_project_path(prerequisite)
        while True:
            previous = json.loads(prerequisite.read_text(encoding='utf-8'))
            if previous['status']=='COMPLETED':
                break
            save_progress(out, manifest, rows, status='WAITING_FOR_M1_M4')
            time.sleep(30)
        verify_frozen(out, manifest)
    completed = {r['run'] for r in rows}
    pending = [j for j in manifest['jobs'] if j['run'] not in completed]
    save_progress(out, manifest, rows)
    print(f"{len(pending)} pending / {len(manifest['jobs'])} total; {args.workers} workers; {out}", flush=True)
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(execute,j,out,manifest['arguments']['timeout_overhead']) for j in pending]
            for future in as_completed(futures):
                row = future.result()
                rows.append(row)
                save_progress(out,manifest,rows)
                print(f"{len(rows)}/{len(manifest['jobs'])} {row['run']}: {row['status']} valid={row['valid']}",flush=True)
        save_progress(out,manifest,rows,status='COMPLETED')
    except BaseException:
        save_progress(out,manifest,rows,status='INTERRUPTED')
        raise
    return out


if __name__=='__main__':
    main()
