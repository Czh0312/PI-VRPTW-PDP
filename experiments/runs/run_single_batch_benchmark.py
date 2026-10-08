"""Frozen M1-M4 exact/ALNS comparison across the current B=1 dataset."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import csv
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.configuration.loader import load_routing_case, resolve_project_path
from experiments.runs.run_multibatch_cycle import (
    INPUT_KEYS, freeze_inputs, source_snapshot, run_worker, write_json)
from models.routing.common.cycle import load_instance
from tools.data.static_batch_dataset import load_manifest, check_pairing


def now():
    return datetime.now().astimezone().isoformat()


def save_csv(path, rows):
    if not rows:
        return
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def mean(values):
    values = [value for value in values if finite(value)]
    return statistics.mean(values) if values else None


def fmt(value, digits=3):
    return '—' if value is None else f'{value:.{digits}f}'


def comparisons(jobs, rows):
    indexed = {row['run']: row for row in rows}
    groups = {}
    for job in jobs:
        groups.setdefault((job['case_id'], job['model']), []).append(job)
    output = []
    for (case_id, model), planned in groups.items():
        present = [indexed[job['run']] for job in planned if job['run'] in indexed]
        if not present:
            continue
        exact = next((row for row in present if row['solver'] == 'exact'), None)
        alns = [row for row in present if row['solver'] == 'alns']
        valid_alns = [row for row in alns if row['valid'] and row['periodic_replay']]
        objectives = [row['objective'] for row in valid_alns]
        exact_objective = exact['objective'] if exact and exact['valid'] and exact['periodic_replay'] else None
        bound = exact.get('best_bound') if exact else None
        bound = bound if finite(bound) and bound >= 0 else None
        best, average = (min(objectives), statistics.mean(objectives)) if objectives else (None, None)
        all_values = objectives + ([exact_objective] if exact_objective is not None else [])
        optimum = exact_objective if exact and exact['proven_optimal'] else None
        def relative(value, reference, denominator=None):
            return (100 * (value-reference) / (denominator if denominator is not None else reference)
                    if value is not None and reference is not None and (denominator or reference) > 0 else None)
        contradictions = []
        tolerance = 1e-6 * max([1.0] + all_values)
        if all_values and bound is not None and bound > min(all_values) + tolerance:
            contradictions.append('lower_bound_exceeds_feasible_solution')
        if optimum is not None and best is not None and best < optimum-tolerance:
            contradictions.append('alns_better_than_proven_optimum')
        first = planned[0]
        output.append(dict(
            case_id=case_id, family_id=first['family_id'], size=first['size'],
            customer_count=first['customer_count'], model=model,
            completed_runs=len(present), planned_runs=len(planned),
            exact_status=exact['status'] if exact else 'PENDING',
            exact_valid=bool(exact and exact['valid']), exact_proven_optimal=bool(exact and exact['proven_optimal']),
            exact_objective=exact_objective, exact_best_bound=bound,
            exact_mip_gap_percent=100*exact['mip_gap'] if exact and finite(exact['mip_gap']) else None,
            exact_solver_seconds=exact.get('solver_runtime') if exact else None,
            exact_process_seconds=exact.get('wall_time') if exact else None,
            alns_completed=len(alns), alns_planned=sum(j['solver']=='alns' for j in planned),
            alns_valid=len(valid_alns), alns_best=best, alns_mean=average,
            alns_worst=max(objectives) if objectives else None,
            alns_population_std=statistics.pstdev(objectives) if objectives else None,
            alns_mean_solver_seconds=mean([row.get('solver_runtime') for row in valid_alns]),
            alns_best_vs_exact_percent=relative(best, exact_objective),
            alns_mean_vs_exact_percent=relative(average, exact_objective),
            alns_best_optimality_gap_percent=relative(best, optimum),
            alns_mean_optimality_gap_percent=relative(average, optimum),
            alns_best_bound_gap_percent=max(0.0, relative(best, bound, best))
                if best is not None and best > 0 and bound is not None else None,
            best_known_objective=min(all_values) if all_values else None,
            consistency_errors=';'.join(contradictions)))
    return output


def save_progress(out, manifest, rows, finished=False):
    rows = sorted(rows, key=lambda row: row['job_index'])
    save_csv(out/'runs.csv', rows)
    compared = comparisons(manifest['jobs'], rows)
    save_csv(out/'comparison.csv', compared)
    errors = [row for row in compared if row['consistency_errors']]
    state = dict(status='COMPLETED' if finished else 'RUNNING', pid=os.getpid(),
                 started_at=manifest['started_at'], updated_at=now(),
                 completed=len(rows), total=len(manifest['jobs']),
                 valid=sum(row['valid'] for row in rows),
                 outcomes=dict(Counter(row['status'] for row in rows)),
                 consistency_errors=errors)
    temporary = out/'progress.json.tmp'
    write_json(temporary, state)
    temporary.replace(out/'progress.json')
    args = manifest['arguments']
    lines = ['# M1–M4 精确求解与 ALNS 规模对比', '',
             f"状态：{state['status']}；进度：{len(rows)}/{len(manifest['jobs'])}。",
             f"数据：{len(manifest['selected_dataset_cases'])} 个 B=1 算例；冻结当前源码、输入和 SHA256。",
             f"精确预算：{args['exact_time']:g} 秒/案例/模型，种子 {args['seeds'][0]}；"
             f"ALNS：{args['time']:g} 秒/种子，种子 {args['seeds']}，迭代上限 {args['iterations']}。",
             f"并发进程 {args['workers']}；每个精确求解器 1 线程；外层超时余量 {args['timeout_overhead']:g} 秒。", '',
             '目标为总距离；两类算法使用同一输入、车队、箱池和时间精度。预算不同，不能据此声称同预算算法优劣。',
             'ALNS 最优/均值/最差统计覆盖预先登记的全部种子，可行数量另列，失败和超时保留。',
             '“相对精确解”可为负，表示 ALNS 优于时限内的精确可行解。只有精确已证最优时，才计算相对最优解的误差。',
             '认证界差采用 (ALNS距离−有效下界)/ALNS距离；它是最优性误差的上界，不能冒充实际最优误差。',
             '无解不等于不可行；未完成的运行不计为失败。随机种子和同源情景不是独立几何样本。', '',
             '| 规模 | 模型 | 精确已完成/计划 | 精确可行 | 已证最优 | ALNS已完成/计划 | ALNS可行 | 最好相对精确 % | 均值相对精确 % | 配对案例数 |',
             '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    aggregates = []
    for size in ('small', 'medium', 'large'):
        for model in range(1,5):
            subset = [r for r in rows if r['size']==size and r['model']==model]
            planned = [j for j in manifest['jobs'] if j['size']==size and j['model']==model]
            if not planned:
                continue
            pairs = [r for r in compared if r['size']==size and r['model']==model
                     and r['completed_runs']==r['planned_runs'] and not r['consistency_errors']]
            exact = [r for r in subset if r['solver']=='exact']
            alns = [r for r in subset if r['solver']=='alns']
            paired = [r for r in pairs if r['alns_best_vs_exact_percent'] is not None]
            item = dict(size=size, model=model, exact_completed=len(exact),
                        exact_planned=sum(j['solver']=='exact' for j in planned),
                        exact_valid=sum(r['valid'] for r in exact), exact_optimal=sum(r['proven_optimal'] for r in exact),
                        alns_completed=len(alns), alns_planned=sum(j['solver']=='alns' for j in planned),
                        alns_valid=sum(r['valid'] for r in alns), paired_cases=len(paired),
                        mean_best_vs_exact_percent=mean([r['alns_best_vs_exact_percent'] for r in paired]),
                        mean_mean_vs_exact_percent=mean([r['alns_mean_vs_exact_percent'] for r in paired]),
                        proven_optimum_pairs=sum(r['alns_best_optimality_gap_percent'] is not None for r in pairs),
                        mean_best_optimality_gap_percent=mean([r['alns_best_optimality_gap_percent'] for r in pairs]),
                        mean_best_bound_gap_percent=mean([r['alns_best_bound_gap_percent'] for r in pairs]))
            aggregates.append(item)
            lines.append(f"| {size} | M{model} | {len(exact)}/{item['exact_planned']} | {item['exact_valid']} | "
                         f"{item['exact_optimal']} | {len(alns)}/{item['alns_planned']} | {item['alns_valid']} | "
                         f"{fmt(item['mean_best_vs_exact_percent'])} | {fmt(item['mean_mean_vs_exact_percent'])} | {len(paired)} |")
    save_csv(out/'summary.csv', aggregates)
    lines += ['', '完整逐次记录：[runs.csv](runs.csv)；逐案例配对：[comparison.csv](comparison.csv)；'
              '分规模汇总：[summary.csv](summary.csv)；运行登记：[manifest.json](manifest.json)。', '',
              f"上下界与最优声明矛盾：{len(errors)} 项。", '',
              '| 顾客数 | 模型 | 案例 | 精确状态 | 精确距离 | 下界 | ALNS可行种子 | ALNS最好 | ALNS均值 | 最好相对精确 % | 最好实际最优误差 % |',
              '| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in compared:
        lines.append(f"| {r['customer_count']} | M{r['model']} | {r['case_id']} | {r['exact_status']} | "
                     f"{fmt(r['exact_objective'])} | {fmt(r['exact_best_bound'])} | "
                     f"{r['alns_valid']}/{r['alns_completed']}/{r['alns_planned']} | {fmt(r['alns_best'])} | "
                     f"{fmt(r['alns_mean'])} | {fmt(r['alns_best_vs_exact_percent'])} | "
                     f"{fmt(r['alns_best_optimality_gap_percent'])} |")
    temporary = out/'report.md.tmp'
    temporary.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    temporary.replace(out/'report.md')
    if finished:
        write_json(out/'verification.json', dict(
            executed_runs=len(rows), planned_runs=len(manifest['jobs']),
            all_jobs_recorded=len(rows)==len(manifest['jobs']),
            all_valid_results_periodic=all(r['periodic_replay'] for r in rows if r['valid']),
            consistency_errors=errors,
            result_sha256={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in out.glob('*/result.json')}))


def execute(job, out, overhead):
    row = run_worker(job, out, overhead)
    path = out/job['run']/'result.json'
    info = json.loads(path.read_text(encoding='utf-8')).get('info', {}) if path.exists() else {}
    row.update(solver_runtime=info.get('runtime'), build_time=info.get('build_time'),
               assignment_strategy=info.get('assignment_strategy'),
               assignment_count=info.get('assignment_count'), assignments_closed=info.get('assignments_closed'))
    write_json(out/(job['run']+'.row.json'), row)
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-manifest', default='data/manifest.json')
    parser.add_argument('--source-kinds', nargs='+', default=['zhou_derived_scenario'])
    parser.add_argument('--sizes', nargs='+', choices=('small','medium','large'))
    parser.add_argument('--families', nargs='+')
    parser.add_argument('--models', nargs='+', type=int, choices=range(1,5), default=[1,2,3,4])
    parser.add_argument('--seeds', nargs='+', type=int, default=[42,43,44])
    parser.add_argument('--exact-time', type=float, default=300)
    parser.add_argument('--time', type=float, default=30)
    parser.add_argument('--iterations', type=int, default=1000000)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--timeout-overhead', type=float, default=60)
    parser.add_argument('--output', required=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    if any(not math.isfinite(x) or x<=0 for x in (args.exact_time,args.time,args.timeout_overhead)):
        parser.error('Budgets must be finite and positive')
    if args.workers<1 or args.iterations<0 or any(s<0 or s>2_000_000_000 for s in args.seeds):
        parser.error('Invalid workers, iterations or seeds')
    out = resolve_project_path(args.output).resolve()
    rows = []
    if args.resume:
        manifest = json.loads((out/'manifest.json').read_text(encoding='utf-8'))
        for relative, digest in manifest['source_sha256'].items():
            if hashlib.sha256((out/'source_snapshot'/relative).read_bytes()).hexdigest()!=digest:
                raise ValueError('Frozen source changed: '+relative)
        for entry in manifest['input_snapshots'].values():
            if hashlib.sha256((out/entry['snapshot']).read_bytes()).hexdigest()!=entry['sha256']:
                raise ValueError('Frozen input changed: '+entry['snapshot'])
        rows = [json.loads(path.read_text(encoding='utf-8')) for path in out.glob('*.row.json')]
        valid_names = {job['run'] for job in manifest['jobs']}
        if any(row['run'] not in valid_names for row in rows) or len({row['run'] for row in rows})!=len(rows):
            raise ValueError('Unexpected/duplicate saved row')
    else:
        _, records = load_manifest(args.dataset_manifest, batch_counts=[1], sizes=args.sizes,
                                   family_ids=args.families, source_kinds=args.source_kinds)
        check_pairing(records)
        records.sort(key=lambda r:(r['customer_count'],r['case_id']))
        variants, jobs = [], []
        for record in records:
            config, fleet = load_routing_case(record['case_path'])
            paths = {resolve_project_path(record['case_path']).resolve()}
            paths.update(resolve_project_path(config[key]).resolve() for key in INPUT_KEYS
                         if isinstance(config.get(key),str))
            hashes = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
            for model in dict.fromkeys(args.models):
                copied = deepcopy(config)
                copied.update(model=model, alns_iter=args.iterations, save_plots=False)
                load_instance(copied,fleet)
                variant_name = f"{record['case_id']}_M{model}"
                variants.append(dict(config=copied,_source_hashes=hashes,name=variant_name))
                methods = [('exact',args.seeds[0],args.exact_time)] + [('alns',s,args.time) for s in dict.fromkeys(args.seeds)]
                for solver,seed,budget in methods:
                    jobs.append(dict(job_index=len(jobs),run=f'{variant_name}_{solver}_seed{seed}',
                                     case_id=record['case_id'],family_id=record['family_id'],
                                     size=record['size'],customer_count=record['customer_count'],
                                     model=model,solver=solver,seed=seed,budget=budget,variant_name=variant_name))
        if args.dry_run:
            print(json.dumps(dict(case_count=len(records),job_count=len(jobs),
                                  solver_budget_seconds=sum(j['budget'] for j in jobs),workers=args.workers),indent=2))
            return
        if out.exists() and any(out.iterdir()):
            parser.error('Refusing to overwrite nonempty output: '+str(out))
        out.mkdir(parents=True,exist_ok=True)
        inputs = freeze_inputs(variants,out,args.dataset_manifest)
        sources = source_snapshot(out)
        (out/'generated_cases').mkdir()
        for variant in variants:
            write_json(out/'generated_cases'/(variant['name']+'.json'),variant['config'])
        for job in jobs:
            job['case_path'] = str(out/'generated_cases'/(job['variant_name']+'.json'))
        manifest = dict(study_version='single_batch_exact_alns_comparison_v1',started_at=now(),
                        arguments=vars(args),python=sys.version,selected_dataset_cases=records,
                        input_snapshots=inputs,source_sha256=sources,jobs=jobs,
                        execution='frozen subprocesses; independent audit and two-cycle replay; no retries',
                        algorithm_changes=False,threads_per_solver=1,
                        solver_budget_seconds=sum(j['budget'] for j in jobs))
        write_json(out/'manifest.json',manifest)
    completed = {row['run'] for row in rows}
    pending = [job for job in manifest['jobs'] if job['run'] not in completed]
    save_progress(out,manifest,rows)
    print(f"{len(pending)} pending / {len(manifest['jobs'])} total; {args.workers} workers; {out}",flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(execute,job,out,manifest['arguments']['timeout_overhead']) for job in pending]
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            save_progress(out,manifest,rows)
            print(f"{len(rows)}/{len(manifest['jobs'])} {row['run']}: {row['status']} "
                  f"valid={row['valid']} distance={row['objective']}",flush=True)
    save_progress(out,manifest,rows,finished=True)
    print('Report: '+str(out/'report.md'),flush=True)
    return out


if __name__=='__main__':
    main()
