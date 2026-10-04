"""Run M1-M4 exact solvers on all selected B=1 cases with frozen inputs/code."""
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
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from configuration.loader import load_routing_case, resolve_project_path
from experiments.runs.run_multibatch_cycle import (
    INPUT_KEYS, freeze_inputs, source_snapshot, run_worker, write_json)
from models.routing.common.cycle import load_instance
from tools.data.static_batch_dataset import load_manifest, check_pairing


def outcome(row):
    if row['valid'] and row['proven_optimal']:
        return 'OPTIMAL'
    if row['proven_infeasible']:
        return 'INFEASIBLE'
    if row['valid']:
        return 'FEASIBLE_NOT_PROVEN'
    if row['status'] in ('TIME_LIMIT', 'TIME_LIMIT_BUILD', 'OUTER_TIMEOUT'):
        return 'NO_SOLUTION_WITHIN_BUDGET'
    return 'ERROR_OR_AUDIT_FAILURE'


def save_progress(out, jobs, rows, started, finished=False):
    rows = sorted(rows, key=lambda row: row['job_index'])
    if rows:
        temporary = out/'runs.csv.tmp'
        with temporary.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(out/'runs.csv')
    state = dict(status='COMPLETED' if finished else 'RUNNING', pid=os.getpid(),
                 started_at=started, updated_at=datetime.now().astimezone().isoformat(),
                 completed=len(rows), total=len(jobs), outcomes=dict(Counter(r['outcome'] for r in rows)))
    temporary = out/'progress.json.tmp'
    write_json(temporary, state)
    temporary.replace(out/'progress.json')
    lines = ['# 单批次 M1–M4 全量精确求解', '',
             'Material Passport: Origin Skill = academic-research-suite / experiment-agent; '
             'Mode = run; Verification Status = '+('EXECUTED' if finished else 'IN_PROGRESS')+'.', '',
             f"进度：{len(rows)}/{len(jobs)}；状态：{state['status']}。",
             '每项运行保留原始状态、可行方案、上下界、证明进度和独立审计；失败及超时不重试、不剔除。', '',
             '| 模型 | 已完成 | 已证明最优 | 可行未证最优 | 已证明不可行 | 时限内无解 | 异常/审计失败 |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for number in sorted({job['model'] for job in jobs}):
        subset = [row for row in rows if row['model'] == number]
        counts = Counter(row['outcome'] for row in subset)
        values = [counts[key] for key in ('OPTIMAL', 'FEASIBLE_NOT_PROVEN', 'INFEASIBLE',
                                          'NO_SOLUTION_WITHIN_BUDGET', 'ERROR_OR_AUDIT_FAILURE')]
        lines.append(f'| M{number} | {len(subset)} | '+' | '.join(map(str, values))+' |')
    lines += ['', '## 预算与结果解释', '',
              f"每个案例×模型预算为 {jobs[0]['budget']:g} 秒，种子 {jobs[0]['seed']}。"
              '预算包含建模时间；外层额外时间见 manifest.json。',
              'M2/M4 的预算覆盖整个分仓枚举，不能把一个子问题最优当作全局最优。',
              '时限内没有可行解不等于证明不可行。有可行解的结果均经过独立审计和两周期重放。',
              '现有派生情景的最优路线不代表完整真实经营数据或经济性结论。', '',
              '[全部运行表](runs.csv) · [运行清单与冻结参数](manifest.json) · [进度](progress.json)', '',
              '## 各案例结果', '',
              '| 案例 | 模型 | 状态 | 距离 | 下界 | Gap | 分仓证明（已闭合/总数） |',
              '| --- | --- | --- | ---: | ---: | ---: | --- |']
    for row in rows:
        gap = '—' if row['mip_gap'] is None else f"{100*row['mip_gap']:.2f}%"
        bound = '—' if row['best_bound'] is None else f"{row['best_bound']:.6f}"
        distance = '—' if row['objective'] is None else f"{row['objective']:.6f}"
        assignment = ('—' if row['assignment_count'] is None else
                      f"{row['assignments_closed']}/{row['assignment_count']}")
        lines.append(f"| {row['case_id']} | M{row['model']} | [{row['outcome']}]({row['run']}/report.md) | "
                     f'{distance} | {bound} | {gap} | {assignment} |')
    temporary = out/'report.md.tmp'
    temporary.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    temporary.replace(out/'report.md')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-manifest', default='data/multibatch/static_suite_v1/manifest.json')
    parser.add_argument('--families', nargs='+')
    parser.add_argument('--sizes', nargs='+', choices=('small', 'medium', 'large'))
    parser.add_argument('--models', nargs='+', type=int, choices=range(1, 5), default=[1, 2, 3, 4])
    parser.add_argument('--exact-time', type=float, default=300)
    parser.add_argument('--timeout-overhead', type=float, default=30)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--output')
    args = parser.parse_args(argv)
    if any(not math.isfinite(t) or t <= 0 for t in (args.exact_time, args.timeout_overhead)):
        parser.error('Budgets must be positive and finite')
    if args.workers < 1 or not 0 <= args.seed <= 2_000_000_000:
        parser.error('Invalid worker count or seed')
    _, selected = load_manifest(args.dataset_manifest, batch_counts=[1],
                                sizes=args.sizes, family_ids=args.families)
    check_pairing(selected)
    selected.sort(key=lambda record: (record['order_count'], record['case_id']))
    variants, jobs = [], []
    for record in selected:
        config, fleet = load_routing_case(record['case_path'])
        instance = load_instance(config, fleet)
        if len(instance.operating_policy.batches) != 1:
            raise ValueError(f"Not single-batch: {record['case_id']}")
        paths = {resolve_project_path(record['case_path']).resolve()}
        paths.update(resolve_project_path(config[key]).resolve() for key in INPUT_KEYS
                     if isinstance(config.get(key), str))
        hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        for number in dict.fromkeys(args.models):
            copied = deepcopy(config)
            copied.update(model=number, solver='exact', exact_time=args.exact_time,
                          seed=args.seed, save_plots=False)
            load_instance(copied, fleet)
            name = f"{record['case_id']}_M{number}_exact_seed{args.seed}"
            variants.append(dict(config=copied, _source_hashes=hashes, run=name))
            jobs.append(dict(job_index=len(jobs), run=name, model=number,
                             case_id=record['case_id'], family_id=record['family_id'],
                             size=record['size'], source_kind=record['source_kind'],
                             order_count=record['order_count'], customer_count=record['customer_count'],
                             batch_count=1, pool=record['default_pool'],
                             assignment_mode='free' if number in (2, 4) else 'fixed_input',
                             solver='exact', seed=args.seed, budget=args.exact_time))
    if args.dry_run:
        plan = dict(case_count=len(selected), job_count=len(jobs), total_budget_seconds=sum(j['budget'] for j in jobs),
                    workers=args.workers, jobs=jobs)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return plan
    out = (resolve_project_path(args.output) if args.output else
           ROOT/'output/experiments/single_batch_exact'/datetime.now().strftime('%Y%m%dT%H%M%S_%f')).resolve()
    if out.exists() and any(out.iterdir()):
        parser.error(f'Refusing to overwrite nonempty output directory: {out}')
    out.mkdir(parents=True, exist_ok=True)
    inputs = freeze_inputs(variants, out, args.dataset_manifest)
    sources = source_snapshot(out)
    (out/'generated_cases').mkdir()
    for variant, job in zip(variants, jobs):
        path = out/'generated_cases'/f"{variant['run']}.json"
        write_json(path, variant['config'])
        job['case_path'] = str(path)
    started = datetime.now().astimezone().isoformat()
    write_json(out/'manifest.json', dict(study_version='single_batch_exact_v1', arguments=vars(args),
        started_at=started, python=sys.version, selected_dataset_cases=selected,
        input_snapshots=inputs, source_sha256=sources, jobs=jobs,
        job_count=len(jobs), total_solver_budget=sum(j['budget'] for j in jobs),
        information_mode='static_all_known', algorithm_changes=False,
        execution='isolated subprocesses; no retries; two-cycle independent audit',
        threads_per_solver=1, comparison='same inputs, resources and budget across M1-M4'))
    rows = []
    save_progress(out, jobs, rows, started)
    print(f'{len(jobs)} jobs; {args.workers} workers; budget {args.exact_time:g}s each; {out}', flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(run_worker, job, out, args.timeout_overhead): job for job in jobs}
        for future in as_completed(futures):
            row = future.result()
            path = out/row['run']/'result.json'
            info = json.loads(path.read_text())['info'] if path.exists() else {}
            for key in ('assignment_count', 'assignments_visited', 'assignments_closed'):
                row[key] = info.get(key)
            row['outcome'] = outcome(row)
            rows.append(row)
            save_progress(out, jobs, rows, started)
            print(f"{len(rows)}/{len(jobs)} {row['run']}: {row['outcome']}; "
                  f"distance={row['objective']}; gap={row['mip_gap']}", flush=True)
    save_progress(out, jobs, rows, started, finished=True)
    print(f'Report: {out / "report.md"}', flush=True)
    return out


if __name__ == '__main__':
    main()
