"""Static-study evidence tables; unknown results are never infeasibility proofs."""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
from statistics import mean


def truth(value):
    return str(value).lower() == 'true'


def number(value):
    return float(value) if value not in ('', None) else None


def write_csv(path, rows):
    if not rows:
        path.write_text('', encoding='utf-8')
        return
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def pool_evidence(rows):
    """N is the only varied input within each scenario_id (including cleaning)."""
    groups = defaultdict(list)
    for row in rows:
        groups[row['scenario_id']].append(row)
    summaries = []
    for scenario, runs in groups.items():
        first = runs[0]
        feasible = sorted({int(r['pool']) for r in runs if truth(r['valid'])})
        excluded = sorted({int(r['pool']) for r in runs if truth(r.get('proven_infeasible'))})
        tested = sorted({int(r['pool']) for r in runs})
        lower = max([int(first['maximum_batch_demand'])]
                    + [int(r['pool_lower_bound']) for r in runs if r.get('pool_lower_bound') not in ('', None)]
                    + [n+1 for n in excluded])
        upper = min(feasible) if feasible else None
        schedule_pools = [int(r['minimum_pool_for_schedule']) for r in runs
                          if truth(r['valid']) and r.get('minimum_pool_for_schedule') not in ('', None)]
        # A valid schedule cannot use fewer containers than a necessary bound.
        conflict = (upper is not None and lower > upper) or bool(schedule_pools and lower > min(schedule_pools))
        summaries.append(dict(scenario_id=scenario, case_id=first['case_id'], family_id=first['family_id'],
                              batch_count=int(first['batch_count']), cleaning_duration=float(first['cleaning_duration']),
                              tested_pools=tested, feasible_tested_pools=feasible, proven_infeasible_pools=excluded,
                              unresolved_tested_pools=[n for n in tested if n not in feasible and n not in excluded],
                              pool_lower_bound=lower, smallest_feasible_tested_pool=upper,
                              smallest_pool_for_found_schedules=min(schedule_pools) if schedule_pools else None,
                              proven_minimum_pool=upper if upper == lower and not conflict else None,
                              evidence_conflict=conflict))
    return summaries


def algorithm_evidence(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['case_variant'], row['solver']].append(row)
    result = []
    for (variant, solver), runs in grouped.items():
        valid = [r for r in runs if truth(r['valid'])]
        distances = [float(r['objective']) for r in valid]
        gaps = [float(r['optimal_gap_percent']) for r in valid if r.get('optimal_gap_percent') not in ('', None)]
        result.append(dict(case_variant=variant, solver=solver, attempts=len(runs), valid_runs=len(valid),
                           feasibility_rate=len(valid)/len(runs),
                           proven_infeasible_runs=sum(truth(r.get('proven_infeasible')) for r in runs),
                           best_feasible_distance=min(distances) if distances else None,
                           mean_feasible_distance=mean(distances) if distances else None,
                           mean_gap_to_proven_optimum=mean(gaps) if gaps else None,
                           mean_wall_seconds=mean(float(r['wall_time']) for r in runs),
                           statuses=json.dumps(dict((status, sum(r['status'] == status for r in runs))
                                                    for status in sorted({r['status'] for r in runs})))))
    return result


def paired_comparisons(rows):
    # Never pair across pools, cleaning assumptions, algorithms or random seeds.
    groups = defaultdict(dict)
    for row in rows:
        if row['family_id']:
            key = (row['family_id'], float(row['cleaning_duration']), int(row['pool']), row['solver'], int(row['seed']))
            batch = int(row['batch_count'])
            if batch in groups[key]:
                raise ValueError('Duplicate run in paired batch comparison')
            groups[key][batch] = row
    comparisons = []
    for (family, cleaning, pool, solver, seed), by_batch in groups.items():
        single = by_batch.get(1)
        for batches, multi in sorted(by_batch.items()):
            if batches == 1:
                continue
            both = single is not None and truth(single['valid']) and truth(multi['valid'])
            comparisons.append(dict(family_id=family, cleaning_duration=cleaning, pool=pool, solver=solver,
                                    seed=seed, multi_batch_count=batches,
                                    single_status=single['status'] if single else 'NOT_RUN', multi_status=multi['status'],
                                    both_valid=both,
                                    single_distance=number(single['objective']) if single and truth(single['valid']) else None,
                                    multi_distance=number(multi['objective']) if truth(multi['valid']) else None,
                                    distance_change=number(multi['objective'])-number(single['objective']) if both else None))
    return comparisons


def summarize_study(directory):
    directory = Path(directory)
    with (directory/'runs.csv').open(encoding='utf-8', newline='') as handle:
        rows = list(csv.DictReader(handle))
    pools = pool_evidence(rows)
    algorithms = algorithm_evidence(rows)
    comparisons = paired_comparisons(rows)
    write_csv(directory/'pool_summary.csv', pools)
    write_csv(directory/'algorithm_summary.csv', algorithms)
    write_csv(directory/'batch_comparisons.csv', comparisons)
    (directory/'pool_summary.json').write_text(json.dumps(pools, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    stocks, cutoffs = [], []
    for row in rows:
        if not truth(row['valid']):
            continue
        result = json.loads((directory/row['run']/'result.json').read_text(encoding='utf-8'))
        seconds = result['time_tick_seconds']
        for entry in result['audit']['clean_stock']:
            stocks.append(dict(run=row['run'], pool=int(row['pool']), **entry,
                               time_minutes=entry['time']*seconds/60))
        for entry in result['audit']['cutoff_snapshots']:
            cutoffs.append(dict(run=row['run'], **{k: v for k, v in entry.items() if k != 'eligible_events'},
                                cutoff_minutes=entry['cutoff']*seconds/60))
    write_csv(directory/'clean_stock.csv', stocks)
    write_csv(directory/'cutoff_inventory.csv', cutoffs)
    lines = ['静态固定分批：容器池与算法验证', '',
             'Material Passport：Origin Skill = academic-research-suite / experiment-agent；'
             'Origin Mode = run；Verification Status = PARTIALLY_VERIFIED（逐次运行状态与审计见表；不外推性能或经济结论）。', '',
             f'共 {len(rows)} 次运行，{sum(truth(r["valid"]) for r in rows)} 个解通过独立审计。',
             '同一场景固定订单、批次、车队、窗口及清洗时间，只改变容器池 N。',
             '必要条件通过不证明可行；启发式未找到解、超时或异常均不证明不可行。', '',
             '| 场景 | 必要池下界 | 最小可行测试池 | 已得时间表最小池 | 已证明最小池 | 未解决测试池 |',
             '| --- | ---: | ---: | ---: | ---: | --- |']
    for row in pools:
        def show(key):
            return '—' if row[key] is None else str(row[key])
        lines.append(f'| {row["scenario_id"]} | {row["pool_lower_bound"]} | '
                     f'{show("smallest_feasible_tested_pool")} | {show("smallest_pool_for_found_schedules")} | '
                     f'{show("proven_minimum_pool")} | {row["unresolved_tested_pools"]} |')
    conflicts = [r['scenario_id'] for r in pools if r['evidence_conflict']]
    if conflicts:
        lines += ['', '证据冲突，不能据此报告最小池证明：'+', '.join(conflicts)]
    lines += ['', '最小池证明仅在有效解与必要下界闭合时成立；下界包含已证不可行 N 的单调性推论，'
              '证明限于当前时间精度、固定分批及趟次上限。', '',
              '[逐次运行与 gap](report.md) · [算法汇总](algorithm_summary.csv) · '
              '[同池单批/多批对照](batch_comparisons.csv) · [逐批清洁库存](clean_stock.csv) · '
              '[各仓冻结量](cutoff_inventory.csv)', '',
              '算法平均距离只统计有效解，并同时报告全部尝试的可行率；不同清洗时间分别列场景。'
              '单批/多批仅匹配相同家族、N、清洗时间、算法及种子，缺少单批运行保留为 NOT_RUN。',
              'manifest.json 在求解前保存完整运行矩阵、预算、输入与源码哈希；工作进程读取冻结副本。']
    (directory/'study_report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return pools


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    summarize_study(parser.parse_args().directory)
