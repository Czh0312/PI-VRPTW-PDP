"""Compare audited M5 post-priced schedules with M6 under matched costs."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import statistics

from tools.configuration.loader import resolve_project_path


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def _write_csv(path, rows):
    if not rows:
        return
    with path.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def cost_change(baseline, candidate):
    if baseline is None or candidate is None:
        return dict(savings=None, savings_percent=None)
    savings = baseline-candidate
    return dict(savings=savings, savings_percent=100*savings/baseline if baseline else None)


def select_baseline(rows):
    """M5's representative must follow its distance objective, including ties."""
    return min((r for r in rows if r['valid'] and r['treatment']=='M5_distance'),
               key=lambda r:(r['distance'], r['cost'], r['seed'], r['solver']), default=None)


def select_candidate(rows):
    return min((r for r in rows if r['valid'] and r['treatment']=='M6_uniform'),
               key=lambda r:(r['cost'], not r['proven_optimal'], r['seed'], r['solver']), default=None)


def summarize(directory, rows=None, records=None):
    out = Path(directory)
    if rows is None:
        rows = json.loads((out/'rows.json').read_text(encoding='utf-8'))
    if records is None:
        records = json.loads((out/'inputs/manifest.json').read_text(encoding='utf-8'))['cases']
    costs = {}
    component_rows = []
    for row in rows:
        if not row['valid']:
            continue
        cost = json.loads((out/row['run']/'comparison_cost.json').read_text(encoding='utf-8'))
        if (not math.isclose(cost['total'], row['cost'], abs_tol=1e-6, rel_tol=1e-9)
                or not math.isclose(sum(cost['components'].values()), cost['total'], abs_tol=1e-6, rel_tol=1e-9)
                or not cost.get('economic_profile_hash')
                or cost['economic_profile_hash'] != row.get('economic_profile_hash')):
            raise ValueError(f'Cost replay or profile hash mismatch: {row["run"]}')
        costs[row['run']] = cost
        component_rows.append(dict(case=row['case'], treatment=row['treatment'], solver=row['solver'],
                                   seed=row['seed'], run=row['run'], currency=cost['currency'],
                                   total=cost['total'], **cost['components']))

    def describe(row):
        if row is None:
            return None
        cost = costs[row['run']]
        reference = cost.get('schedule_minimum_pool_reference')
        return dict(run=row['run'], solver=row['solver'], seed=row['seed'], cost=row['cost'],
                    cost_with_schedule_minimum_pool=reference['total'] if reference else None,
                    initial_pool_investment=cost['initial_pool_investment'],
                    pool=row['pool'], minimum_pool_for_schedule=row['minimum_pool_for_schedule'],
                    delivery_batches=row['batches'], distance=row['distance'],
                    e1_trips=row['e1_trips'], e2_trips=row['e2_trips'],
                    components=cost['components'], status=row['status'],
                    proven_optimal=row['proven_optimal'], proof_objective=('distance'
                        if row['treatment']=='M5_distance' else 'cost'))

    cases, comparisons, pairs = [], [], []
    for record in records:
        group = [r for r in rows if r['case']==record['name']]
        priced = [costs[r['run']] for r in group if r['valid']]
        contracts = {(c['economic_profile_hash'], c['currency'], c['evaluation_period'],
                      c['cost_basis'], c['work_time_basis']) for c in priced}
        if len(contracts) > 1:
            raise ValueError(f'M5/M6 require the same economic profile and evaluation period: {record["name"]}')
        m5 = select_baseline(group)
        m6 = select_candidate(group)
        base, joint = describe(m5), describe(m6)
        change = cost_change(base['cost'] if base else None, joint['cost'] if joint else None)
        minimum_change = cost_change(base['cost_with_schedule_minimum_pool'] if base else None,
                                     joint['cost'] if joint else None)
        methods = {}
        for method in sorted({r['solver'] for r in group}):
            stats = {}
            for treatment in ('M5_distance', 'M6_uniform'):
                attempted = [r for r in group if r['solver']==method and r['treatment']==treatment]
                values = [r['cost'] for r in attempted if r['valid']]
                stats[treatment] = dict(attempted=len(attempted), valid=len(values),
                    best=min(values) if values else None, mean=statistics.mean(values) if values else None,
                    worst=max(values) if values else None,
                    stddev=statistics.stdev(values) if len(values)>1 else None)
            methods[method] = stats
        for method, seed in sorted({(r['solver'], r['seed']) for r in group}):
            first = next((r for r in group if r['solver']==method and r['seed']==seed
                          and r['treatment']=='M5_distance'), None)
            second = next((r for r in group if r['solver']==method and r['seed']==seed
                           and r['treatment']=='M6_uniform'), None)
            first_cost = first['cost'] if first and first['valid'] else None
            second_cost = second['cost'] if second and second['valid'] else None
            pairs.append(dict(case=record['name'], solver=method, seed=seed,
                              m5_status=first['status'] if first else 'NOT_RUN',
                              m6_status=second['status'] if second else 'NOT_RUN',
                              m5_cost=first_cost, m6_cost=second_cost,
                              **cost_change(first_cost, second_cost)))
        currency = priced[0]['currency'] if priced else None
        cases.append(dict(case=record['name'], currency=currency,
                          economic_profile_hash=priced[0]['economic_profile_hash'] if priced else None,
                          m5=base, m6=joint, comparison=change,
                          schedule_minimum_pool_comparison=minimum_change, methods=methods))
        comparisons.append(dict(case=record['name'], currency=currency,
            m5_run=base['run'] if base else None, m6_run=joint['run'] if joint else None,
            m5_cost=base['cost'] if base else None, m6_cost=joint['cost'] if joint else None,
            **change, m5_configured_pool=base['pool'] if base else None,
            m5_schedule_minimum_pool=base['minimum_pool_for_schedule'] if base else None,
            m5_cost_with_schedule_minimum_pool=base['cost_with_schedule_minimum_pool'] if base else None,
            savings_vs_schedule_minimum_pool=minimum_change['savings'],
            savings_percent_vs_schedule_minimum_pool=minimum_change['savings_percent'],
            m6_pool=joint['pool'] if joint else None))
    payload = dict(schema_version=1, baseline_selection='minimum_distance_then_cost',
                   candidate_selection='minimum_observed_cost',
                   planned_runs=len(rows), valid_runs=sum(r['valid'] for r in rows), cases=cases,
                   paired_runs=pairs, limitations=[
                       'M5 remains a distance optimization; its cost is calculated after solving',
                       'Primary M5 cost uses its configured pool; minimum schedule pool is a separate reference',
                       'Savings are M5 cost minus M6 cost; negative values are cost increases',
                       'Cost coefficients may be synthetic; currency and source type remain in per-run outputs',
                       'Calendar and objective both change; no isolated causal attribution',
                       'Observed best runs do not establish convergence or statistical significance'])
    _write_json(out/'cost_summary.json', payload)
    _write_csv(out/'cost_comparison.csv', comparisons)
    _write_csv(out/'cost_pairs.csv', pairs)
    _write_csv(out/'cost_components.csv', component_rows)

    def number(value):
        return f'{value:.4f}' if value is not None else '—'

    lines = ['# 模型五与模型六成本对比', '',
        'M5以距离为目标，求解结束并通过独立审计后，按与M6相同的费用配置计算成本。M5代表方案按最短距离选取，同距离时按成本排序；M6按已观察到的最低成本选取。', '',
        '主对比使用M5实际配置的箱池。另列同一路线与时间表的最低需箱量计价，作为箱池配置的参考，不改写M5求解结果，也不代表另求得的成本最优方案。', '',
        '| 算例 | 单位 | M5成本 | M6成本 | 节省额 | 节省比例% | M5同时间表最低箱池成本 | 相对此参考节省% |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for c in cases:
        base, joint = c['m5'], c['m6']
        lines.append(f"| {c['case']} | {c['currency'] or '—'} | {number(base['cost'] if base else None)} | "
                     f"{number(joint['cost'] if joint else None)} | {number(c['comparison']['savings'])} | "
                     f"{number(c['comparison']['savings_percent'])} | "
                     f"{number(base['cost_with_schedule_minimum_pool'] if base else None)} | "
                     f"{number(c['schedule_minimum_pool_comparison']['savings_percent'])} |")
    lines += ['', '节省比例=(M5成本−M6成本)/M5成本×100%；负数表示M6成本更高，M5成本为零时比例留空。购置投入单独报告，不叠加到周期成本。', '',
              '## 每种求解方法的全部运行', '',
              '| 算例 | 方法 | 模型 | 有效/尝试 | 最低成本 | 平均成本 | 最高成本 | 样本标准差 |',
              '| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for c in cases:
        for method, groups in c['methods'].items():
            for label, stats in groups.items():
                lines.append(f"| {c['case']} | {method} | {label} | {stats['valid']}/{stats['attempted']} | "
                             + ' | '.join(number(stats[key]) for key in ('best', 'mean', 'worst', 'stddev'))+' |')
    lines += ['', '同方法同种子的逐对成本及失败状态见cost_pairs.csv，逐项费用见cost_components.csv，代表方案、证明目标和购置投入见cost_summary.json。原始结果、重放审计和comparison_cost.json保存在每次运行目录。', '',
              '两模型的费用配置哈希与评价期必须一致。均匀日历与目标函数同时变化，结果用于整体方案对比。合成单位不换算为真实人民币收益；限时方案不称为成本最优。', '']
    (out/'cost_report.md').write_text('\n'.join(lines), encoding='utf-8')
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    args = parser.parse_args(argv)
    out = resolve_project_path(args.directory)
    summarize(out)
    print(f'Cost comparison saved: {out}')


if __name__=='__main__':
    main()
