"""Score completed ALNS trials offline; exact references never enter search."""
import argparse
import csv
import math
from pathlib import Path
import statistics
import xml.etree.ElementTree as ET
import zipfile
from run import HERE, ROOT, read, save, sha


def collect():
    rows = []
    for path in sorted((HERE / 'trials' / 'confirmatory').rglob('summary.json')):
        row = read(path)
        row['folder'] = str(path.parent)
        trace = read(path.parent / 'result.json').get('anytime', [])
        row['first_audited_feasible_return_seconds'] = next((point['elapsed'] for point in trace
            if point.get('objective') is not None), None)
        if row['case'].startswith('S'):
            reference = read(ROOT / f'output/experiments/m8_all_small_rerun_20261029_v1/runs/{row["case"]}/s42/summary.json')
            assert reference['proven_optimal']
            optimum = reference['audited_upper_bound']
            row['reference_optimum'] = optimum
            if row['audit_valid']:
                assert row['objective'] >= optimum - 1e-6
                row['gap_percent'] = max(0., row['objective']-optimum)/optimum*100
                row['hit_reference_optimum'] = abs(row['objective']-optimum) <= 1e-6
            else:
                row['gap_percent'] = None
                row['hit_reference_optimum'] = False
            row['within_one_percent'] = row['gap_percent'] is not None and row['gap_percent'] <= 1.
            row['first_audited_return_within_one_percent_seconds'] = next((point['elapsed'] for point in trace
                if point.get('objective') is not None and point['objective'] <= optimum*1.01+1e-6), None)
            row['first_audited_return_at_optimum_seconds'] = next((point['elapsed'] for point in trace
                if point.get('objective') is not None and abs(point['objective']-optimum) <= 1e-6), None)
        else:
            row.update(reference_optimum=None, gap_percent=None, hit_reference_optimum=None,
                within_one_percent=None, first_audited_return_within_one_percent_seconds=None,
                first_audited_return_at_optimum_seconds=None)
        rows.append(row)
    return rows


def statistics_of(rows):
    output = {}
    for variant in ['baseline', 'candidate']:
        group = [r for r in rows if r['variant'] == variant]
        small = [r for r in group if r['case'].startswith('S')]
        gaps = [r['gap_percent'] for r in small if r['gap_percent'] is not None]
        output[variant] = dict(trials=len(group), small_trials=len(small),
            feasible=sum(r['audit_valid'] for r in group), small_feasible=sum(r['audit_valid'] for r in small),
            mean_small_gap_percent=statistics.mean(gaps) if gaps else None,
            median_small_gap_percent=statistics.median(gaps) if gaps else None,
            worst_small_gap_percent=max(gaps) if gaps else None,
            optimum_hits=sum(r['hit_reference_optimum'] is True for r in small),
            within_one_percent=sum(r['within_one_percent'] is True for r in small),
            total_evaluations=sum(r['counters']['evaluations'] for r in group),
            total_physical_audits=sum(r['counters']['physical_audits'] for r in group),
            mean_solve_wall_seconds=statistics.mean(r['solve_wall_seconds'] for r in group) if group else None,
            mean_solve_cpu_seconds=statistics.mean(r['solve_cpu_seconds'] for r in group) if group else None)
    pairs = []
    keys = sorted({(r['case'], r['seed']) for r in rows})
    for case, seed in keys:
        pair = {r['variant']: r for r in rows if (r['case'], r['seed']) == (case, seed)}
        if set(pair) != {'baseline', 'candidate'}:
            continue
        a, b = pair['baseline'], pair['candidate']
        if a['audit_valid'] and b['audit_valid']:
            delta = b['objective']-a['objective']
            outcome = 'better' if delta < -1e-6 else 'worse' if delta > 1e-6 else 'tie'
        else:
            outcome, delta = 'incomplete_or_infeasible', None
        pairs.append(dict(case=case, seed=seed, outcome=outcome, candidate_minus_baseline_cost=delta,
                          baseline_objective=a['objective'], candidate_objective=b['objective']))
    output['paired_counts'] = {key: sum(p['outcome'] == key for p in pairs)
                               for key in ['better', 'tie', 'worse', 'incomplete_or_infeasible']}
    output['small_paired_counts'] = {key: sum(p['outcome'] == key and p['case'].startswith('S') for p in pairs)
                                     for key in ['better', 'tie', 'worse', 'incomplete_or_infeasible']}
    return output, pairs


def verify_plan(phase, driver):
    plan = read(HERE / f'{phase}_plan.json')
    assert sha(HERE / driver) == plan['driver_sha256']
    for variant, files in plan['sources'].items():
        for name, digest in files.items():
            assert sha(HERE / 'sources' / variant / name) == digest, name
    return plan


def finalize():
    plan = verify_plan('confirmatory', 'run.py')
    verify_plan('pilot', 'pilot_driver.py')
    rows = collect()
    assert len(rows) == len(plan['jobs']) == 62
    for job in plan['jobs']:
        assert sum(all(row[key] == job[key] for key in ['case', 'seed', 'variant', 'phase', 'seconds']) for row in rows) == 1
    stats, pairs = statistics_of(rows)
    corpus_count = 0
    for case in ['S02', 'S03', 'S09', 'M01', 'L01']:
        a, b = [read(HERE / 'replays' / v / f'{case}.json') for v in ['baseline', 'candidate']]
        assert a['value_sha256'] == b['value_sha256']
        corpus_count += a['records']
    cpu = read(HERE / 'paired_replay.json')
    original_cpu = sum(r['cpu_median_seconds']['baseline'] for r in cpu)
    candidate_cpu = sum(r['cpu_median_seconds']['candidate'] for r in cpu)
    stats['fixed_corpus'] = dict(exactly_equal_evaluations=corpus_count,
        summed_old_case_median_cpu_seconds=original_cpu, summed_new_case_median_cpu_seconds=candidate_cpu,
        cpu_reduction_percent=100*(1-candidate_cpu/original_cpu), exploratory_timing=True)
    small_cpu = [row for row in cpu if row['case'].startswith('S')]
    stats['fixed_corpus']['small_only_cpu_reduction_percent'] = 100*(1-
        sum(row['cpu_median_seconds']['candidate'] for row in small_cpu)/
        sum(row['cpu_median_seconds']['baseline'] for row in small_cpu))
    stats['published_scope'] = dict(max_fast_path_orders=9, quality_validation_orders=7,
        larger_instances_use_original_evaluation=True,
        original_62_trials_used_all_size_prototype=True)
    save(HERE / 'statistics.json', stats)
    save(HERE / 'pairs.json', pairs)
    with (HERE / 'results.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        fields = [k for k in rows[0] if k not in ('counters',)]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: row[k] for k in fields} for row in rows)
    lines = ['# 模型八 ALNS：单次完整评价审计优化', '',
        '**发布范围：新快评默认仅用于不超过9单的小实例，正式质量验证覆盖七单算例；超过9单保留原评价路径。**'
        '以下62次对照考察的是最初全规模候选。M01两组、L01一组出现成本回退，因此发布时加入规模保护，'
        '并将发布版再次对全部8,366次冻结候选逐项回放验证；没有把中大例回退结果隐藏或改写。', '',
        '2026-10-29。候选版先根据实际总仓清洗完成时间推算候选箱池，然后在该箱池下运行完整物理、费用和两周期审计。'
        '正常可行且没有命中评价缓存的候选从两次完整物理审计变为一次。推算不成立、完整审计失败或超过箱池上限时，'
        '回退原评价路径，保留原有错误码及罚分。业务规则、费用、搜索算子、K 域和参考最优值的使用方式未改。', '',
        '## 正确性与固定候选计算', '',
        f'五个来源 S02/S03/S09/M01/L01 共 {corpus_count:,} 次真实候选评价回放：原版与候选版的分数、'
        '完整返回方案及错误信息的规范化 SHA256 逐项相同。另以专项测试检查库存同时刻边界、'
        '错误数量、候选失败回退、原九例方案与完整两周期审计仍执行。', '',
        f'交替回放中，五例 CPU 中位数合计从 {original_cpu:.6f} 秒降到 {candidate_cpu:.6f} 秒，'
        f'减少 {100*(1-candidate_cpu/original_cpu):.2f}%。这是同一批固定候选的评价计算，不能等同于整个 ALNS 的加速倍数。'
        f'其中三组小例固定候选的 CPU 中位数合计减少 {stats["fixed_corpus"]["small_only_cpu_reduction_percent"]:.2f}%。'
        '最初墙钟样本有明显波动，其完整数据保存在 paired_replay_wall_only.json；后续同时记录 CPU 与墙钟。'
        '这些计时属于探索性定位，保留全部轮次，不把其中最好的一轮当作最终性能。', '',
        '## 冻结后的同预算搜索对照', '',
        '原版和候选版各自从原始输入开始搜索；无外部初解，无精确求解器调用，无参考最优值或下界输入。'
        '九个七单小例，各用新种子 11/23/37，单次总搜索预算 20 秒，共 54 次；'
        'M01、L01 各补两个种子，共 8 次，合计 62 次。单个搜索进程、串行执行，配对先后顺序交替。'
        '调用前预加载相同模块，求解时间包含构造、搜索、精修及内部审计，最后独立复核在预算外。', '',
        '| 指标（27 个小例运行） | 原版 | 候选版 |', '|---|---:|---:|']
    a, b = stats['baseline'], stats['candidate']
    metrics = [('可行且两周期审计通过', 'small_feasible', ''), ('参考最优值命中次数', 'optimum_hits', ''),
               ('最终误差不超过 1% 的次数', 'within_one_percent', ''),
               ('平均最优差距', 'mean_small_gap_percent', '%'), ('中位最优差距', 'median_small_gap_percent', '%'),
               ('最差最优差距', 'worst_small_gap_percent', '%')]
    for label, key, unit in metrics:
        av, bv = a[key], b[key]
        lines.append(f'| {label} | {av:.4f}{unit} | {bv:.4f}{unit} |' if unit else f'| {label} | {av}/27 | {bv}/27 |')
    counts = stats['small_paired_counts']
    lines += ['', f'27 组小例配对：候选版成本更低 {counts["better"]} 组，相同 {counts["tie"]} 组，更高 {counts["worse"]} 组，'
              f'不完整/不可行 {counts["incomplete_or_infeasible"]} 组。全部结果保留在 pairs.json 和 results.csv。', '',
              '| 算例 | 原版平均最优差距 | 候选平均最优差距 | 原版最差差距 | 候选最差差距 |',
              '|---|---:|---:|---:|---:|']
    for case in [f'S{i:02}' for i in range(1, 10)]:
        values = {v: [r['gap_percent'] for r in rows if r['case'] == case and r['variant'] == v and r['gap_percent'] is not None]
                  for v in ['baseline', 'candidate']}
        x, y = values['baseline'], values['candidate']
        lines.append(f'| {case} | {statistics.mean(x):.4f}% | {statistics.mean(y):.4f}% | {max(x):.4f}% | {max(y):.4f}% |')
    lines += ['', '参考差距为 `(本次可行成本−独立已证最优值)/独立已证最优值`。精确参考仅在搜索完成后由分析程序读取；'
        'ALNS 命中该值不等于 ALNS 自己给出了全局最优证明。results.csv 同时保留首次可行、'
        '首次进入 1% 和首次命中参考最优的审计候选返回时间；粒度为外层候选返回，不将其解释为内部首次发现的精确时刻。', '',
        '## 中大例检查', '', '| 算例 | 种子 | 原版成本 | 候选成本 | 候选−原版 |', '|---|---:|---:|---:|---:|']
    for pair in pairs:
        if not pair['case'].startswith('S'):
            lines.append(f'| {pair["case"]} | {pair["seed"]} | {pair["baseline_objective"]:.6f} | '
                         f'{pair["candidate_objective"]:.6f} | {pair["candidate_minus_baseline_cost"]:.6f} |')
    lines += ['', '中大例没有独立已证最优值，不报告最优性差距；这里只比较本次可行成本。'
        '20 秒属于短预算验证，不能代替正式 300/600/1200 秒或更多来源、更多种子的质量实验。', '',
        '## 测试、默认入口和证据', '',
        '测试计数、所选范围和默认替换状态另见 validation.json 与 promotion.json；没有运行全仓库测试。'
        '原版和候选版的 297 个 Python 源文件都已冻结，基线哈希见 baseline_source_hashes.json，'
        '正式顺序和输入信息约定见 confirmatory_plan.json。试跑使用旧 driver 的确切副本 pilot_driver.py，'
        '仅探索阶段使用种子 42；正式种子在冻结后执行。', '',
        '本次改动目标是降低候选评价成本。对每个算例、每个种子的同预算质量不作逐次改善保证；'
        '后续可继续针对路线、分批与截点的联合邻域改进搜索质量。', '']
    (HERE / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json_compact(stats), flush=True)


def json_compact(value):
    import json
    return json.dumps(value, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['status', 'finalize'])
    args = parser.parse_args()
    if args.action == 'finalize':
        finalize()
    else:
        rows = collect()
        print(json_compact(dict(completed=len(rows), expected=62, statistics=statistics_of(rows)[0])))
