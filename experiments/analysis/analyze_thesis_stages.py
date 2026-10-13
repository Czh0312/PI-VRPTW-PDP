"""Reproducible primary-attempt tables and figures for thesis stage reports.

Run with --study <frozen study>. Incomplete stages are labelled preliminary;
missing/failed rows remain in denominators and are never assigned zero cost.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from itertools import combinations
import math
from pathlib import Path
import statistics

from experiments.runs import run_thesis_completion as driver


def table(path, rows, fields=None):
    fields = fields or list(dict.fromkeys(k for row in rows for k in row))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def primary_rows(study):
    manifest = driver.read(study/'manifest.json')
    driver.verify(study, manifest)
    rows = []
    for job in manifest['jobs']:
        paths = driver.attempt_dirs(study, job)
        if paths and driver.complete_attempt(paths[0]):
            row = driver.read(paths[0]/'row.json')
            row['_path'] = paths[0]
        else:
            row = driver.terminal_row(job, 'RUNNING' if paths else 'NOT_RUN')
        rows.append(row)
    return manifest, rows


def grouped_statistics(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row['case'], row['model'], row['method'], row['version'], row['budget'],
               row.get('group') or '', row.get('fixed_pool') if row.get('fixed_pool') is not None else -1].append(row)
    output = []
    for (case, model, method, version, budget, group, fixed_pool), members in sorted(groups.items()):
        valid = [r for r in members if r['valid']]
        costs = [r['objective'] for r in valid]
        times = [r['solve_wall_seconds'] for r in members if r.get('solve_wall_seconds') is not None]
        output.append(dict(case=case, family=members[0].get('family'), model=model, method=method,
            version=version, budget=budget, group=group or None, fixed_pool=fixed_pool if fixed_pool != -1 else None,
            planned=len(members), valid=len(valid),
            feasible_rate=len(valid)/len(members), mean=statistics.mean(costs) if costs else None,
            stdev=statistics.stdev(costs) if len(costs) > 1 else None,
            best=min(costs) if costs else None, worst=max(costs) if costs else None,
            mean_call_seconds=statistics.mean(times) if times else None,
            mean_budget_utilization=statistics.mean(times)/budget if times else None,
            terminal=sum(r['status'] not in ('NOT_RUN', 'RUNNING') for r in members),
            proven_optimal=sum(r['proven_optimal'] for r in members),
            proven_infeasible=sum(r['proven_infeasible'] for r in members)))
    return output


def paired_versions(rows, versions):
    if len(versions) != 2:
        return []
    pairs = defaultdict(dict)
    for row in rows:
        key = (row['case'], row['model'], row['method'], row['seed'], row['budget'],
               row.get('group') or '', row.get('fixed_pool') if row.get('fixed_pool') is not None else -1)
        if row['version'] in pairs[key]:
            raise ValueError('Duplicate version in matched comparison')
        pairs[key][row['version']] = row
    output = []
    for key, values in sorted(pairs.items()):
        a, b = (values.get(v) for v in versions)
        both = a is not None and b is not None and a['valid'] and b['valid']
        output.append(dict(case=key[0], model=key[1], method=key[2], seed=key[3], budget=key[4],
            group=key[5] or None, fixed_pool=key[6] if key[6] != -1 else None,
            first_version=versions[0], second_version=versions[1],
            first_status=a['status'] if a else 'NOT_PLANNED', second_status=b['status'] if b else 'NOT_PLANNED',
            both_valid=bool(both), first_cost=a['objective'] if a and a['valid'] else None,
            second_cost=b['objective'] if b and b['valid'] else None,
            first_minus_second=a['objective']-b['objective'] if both else None,
            relative_improvement=(a['objective']-b['objective'])/a['objective'] if both and a['objective'] else None))
    return output


def paired_budgets(rows):
    """Match budgets within the same instance/model/version/seed, including failures."""
    groups = defaultdict(dict)
    for row in rows:
        key = (row['case'], row['model'], row['method'], row['version'], row['seed'],
               row.get('group') or '', row.get('fixed_pool') if row.get('fixed_pool') is not None else -1)
        if row['budget'] in groups[key]:
            raise ValueError('Duplicate instance/version/seed/budget in budget comparison')
        groups[key][row['budget']] = row
    output = []
    for key, members in sorted(groups.items()):
        for small, large in combinations(sorted(members), 2):
            a, b = members[small], members[large]
            if (a.get('case_path') != b.get('case_path') or a.get('counts') != b.get('counts')):
                raise ValueError('Budget comparison changed frozen input or K domain')
            both = a['valid'] and b['valid']
            output.append(dict(case=key[0], model=key[1], method=key[2], version=key[3], seed=key[4],
                group=key[5] or None, fixed_pool=key[6] if key[6] != -1 else None,
                short_budget=small, long_budget=large, short_status=a['status'], long_status=b['status'],
                short_valid=a['valid'], long_valid=b['valid'], both_valid=both,
                short_cost=a['objective'] if a['valid'] else None,
                long_cost=b['objective'] if b['valid'] else None,
                short_minus_long=a['objective']-b['objective'] if both else None,
                relative_improvement=(a['objective']-b['objective'])/a['objective'] if both and a['objective'] else None,
                short_call_seconds=a.get('solve_wall_seconds'), long_call_seconds=b.get('solve_wall_seconds')))
    return output


def diagnostics(rows):
    output = []
    for row in rows:
        if '_path' not in row:
            continue
        path = row['_path']
        if not (path/'search_diagnostics.json').exists():
            continue
        diag = driver.read(path/'search_diagnostics.json')
        if not diag.get('enabled'):
            continue
        candidates = driver.read(path/'candidate_diagnostics.json')
        attempts, improvements, accepted = defaultdict(int), defaultdict(int), 0
        for candidate in candidates:
            info = candidate['info']
            accepted += info.get('accepted_moves', 0)
            for key, value in info.get('operator_attempts', {}).items():
                attempts[key] += value
            for key, value in info.get('operator_improvements', {}).items():
                improvements[key] += value
        discoveries = diag['improvements']
        output.append(dict(case=row['case'], model=row['model'], seed=row['seed'], split=row.get('split'),
            version=row['version'], budget=row['budget'], group=row.get('group'), fixed_pool=row.get('fixed_pool'),
            status=row['status'], valid=row['valid'], objective=row['objective'],
            evaluation_calls=diag['evaluation_calls'], valid_evaluations=diag['valid_evaluations'],
            valid_evaluation_fraction=diag['valid_evaluations']/diag['evaluation_calls'] if diag['evaluation_calls'] else None,
            cache_hits=diag['cache_hits'], evaluation_seconds=diag['evaluation_seconds'],
            construction_calls=diag['construction_calls'], construction_successes=diag['construction_successes'],
            construction_seconds=diag['construction_seconds'], accepted_moves=accepted,
            first_candidate_seconds=discoveries[0]['elapsed'] if discoveries else None,
            first_candidate_cost=discoveries[0]['objective'] if discoveries else None,
            best_candidate_seconds=discoveries[-1]['elapsed'] if discoveries else None,
            violations=str(diag['violations']), operator_attempts=str(dict(attempts)),
            operator_improvements=str(dict(improvements))))
    return output


def ablation_contrasts(rows):
    groups = defaultdict(dict)
    for row in rows:
        if row.get('group') in ('A', 'B', 'C', 'D'):
            key = (row['case'], row['seed'], row['method'], row['budget'], row['model'], row['version'])
            if row['group'] in groups[key]:
                raise ValueError('Duplicate ablation group in matched comparison')
            groups[key][row['group']] = row
    output = []
    for key, members in sorted(groups.items()):
        record = dict(case=key[0], seed=key[1], method=key[2], budget=key[3], model=key[4], version=key[5])
        costs = {g: r['objective'] for g, r in members.items() if r['valid']}
        bounds = {}
        for g, r in members.items():
            lower = r['objective'] if r['valid'] and r['proven_optimal'] else r.get('best_bound')
            if lower is not None and math.isfinite(lower):
                bounds[g] = lower
        record.update({f'{g}_status': members[g]['status'] if g in members else 'NOT_PLANNED' for g in 'ABCD'})
        for g in 'ABCD':
            record[f'{g}_upper'] = costs.get(g)
            record[f'{g}_lower'] = bounds.get(g)
            record[f'{g}_proven_optimal'] = members[g]['proven_optimal'] if g in members else False
        for a, b in ('AB', 'AC', 'CD', 'BD', 'AD'):
            record[f'{a}_minus_{b}'] = costs[a]-costs[b] if a in costs and b in costs else None
            record[f'{a}_minus_{b}_lower'] = bounds[a]-costs[b] if a in bounds and b in costs else None
            record[f'{a}_minus_{b}_upper'] = costs[a]-bounds[b] if a in costs and b in bounds else None
            # In the registered E2 design b relaxes a. An incumbent reversal
            # alone diagnoses search quality, not a contradiction of inclusion.
            tolerance = 1e-6+abs(costs.get(a, 0))*1e-7
            record[f'{a}_to_{b}_incumbent_reversal'] = a in costs and b in costs and costs[b] > costs[a]+tolerance
            record[f'{a}_to_{b}_certificate_conflict'] = a in costs and b in members and (
                members[b]['proven_infeasible'] or b in bounds and bounds[b] > costs[a]+tolerance)
        record['interaction_A_minus_B_minus_C_plus_D'] = costs['A']-costs['B']-costs['C']+costs['D'] if len(costs) == 4 else None
        record['interaction_lower'] = bounds['A']-costs['B']-costs['C']+bounds['D'] if all(g in bounds for g in 'AD') and all(g in costs for g in 'BC') else None
        record['interaction_upper'] = costs['A']-bounds['B']-bounds['C']+costs['D'] if all(g in costs for g in 'AD') and all(g in bounds for g in 'BC') else None
        record['all_four_valid'] = len(costs) == 4
        output.append(record)
    return output


def figures(study, rows, stats):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    directory = study/'figures'
    directory.mkdir(exist_ok=True)
    groups = defaultdict(list)
    for row in rows:
        if row.get('solve_wall_seconds') is not None:
            groups[f'M{row["model"]} {row["method"]} {row["version"]}'].append(row['solve_wall_seconds']/row['budget'])
    if groups:
        groups = dict(sorted(groups.items()))
        fig, ax = plt.subplots(figsize=(max(7, len(groups)*1.1), 4), constrained_layout=True)
        ax.boxplot(list(groups.values()), labels=list(groups), showmeans=True)
        ax.axhline(1, color='gray', ls='--', lw=1)
        ax.set_ylabel('Wall time / call budget')
        ax.tick_params(axis='x', rotation=35)
        fig.savefig(directory/'budget_utilization.png', dpi=160)
        fig.savefig(directory/'budget_utilization.pdf')
        plt.close(fig)
    # One panel per instance/model/method/budget; global curves remain raw
    # observations, with no interpolation before the first confirmed solution.
    curves = defaultdict(list)
    for row in rows:
        if '_path' in row and (row['_path']/'anytime.csv').exists():
            curves[row['case'], row['model'], row['method'], row['budget'],
                   row.get('group') or '', row.get('fixed_pool') if row.get('fixed_pool') is not None else -1].append(row)
    for index, (key, members) in enumerate(sorted(curves.items())):
        fig, ax = plt.subplots(figsize=(7, 4), constrained_layout=True)
        count = 0
        for row in members:
            with (row['_path']/'anytime.csv').open(encoding='utf-8-sig') as handle:
                values = [r for r in csv.DictReader(handle) if r.get('objective')]
            if values:
                ax.step([float(r['elapsed']) for r in values], [float(r['objective']) for r in values],
                        where='post', label=f'{row["version"]} s{row["seed"]}', alpha=.8)
                count += 1
        if count:
            restriction = (f' / group {key[4]}' if key[4] else '') + (f' / N={key[5]}' if key[5] != -1 else '')
            ax.set(title=f'{key[0]} / M{key[1]} / {key[2]} / {key[3]} s{restriction}', xlabel='Global elapsed seconds', ylabel='Audited objective')
            ax.legend(fontsize=7, ncol=2)
            fig.savefig(directory/f'anytime_{index:03d}.png', dpi=130)
        plt.close(fig)


def analyze(study, plots=True):
    study = Path(study).resolve()
    manifest, rows = primary_rows(study)
    stats = grouped_statistics(rows)
    table(study/'tables/per_instance_statistics.csv', stats)
    pairs = paired_versions(rows, manifest['source_versions'])
    table(study/'tables/paired_versions.csv', pairs)
    budget_pairs = paired_budgets(rows)
    table(study/'tables/paired_budgets.csv', budget_pairs)
    diagnostic_rows = diagnostics(rows)
    table(study/'tables/search_diagnostics.csv', diagnostic_rows)
    contrasts = ablation_contrasts(rows)
    table(study/'tables/ablation_contrasts.csv', contrasts)
    # Flatten components for downstream thesis tables without rounding the raw evidence.
    table(study/'tables/cost_components.csv', [dict(id=r['id'], case=r['case'], model=r['model'],
        group=r.get('group'), version=r['version'], seed=r['seed'], status=r['status'], valid=r['valid'],
        objective=r['objective'], pool=r.get('pool'), minimum_pool=r.get('minimum_pool'),
        demand=r.get('demand'), k=r.get('k'), delivery_batches=r.get('delivery_batches'),
        **r.get('cost_components', {})) for r in rows])
    if plots:
        figures(study, rows, stats)
    done = sum(r['status'] not in ('NOT_RUN', 'RUNNING') for r in rows)
    valid = sum(r['valid'] for r in rows)
    lines = ['# 阶段结果分析', '', f'阶段 {manifest["phase"]}；完成 {done}/{len(rows)}，审计可行 {valid}/{len(rows)}。',
        '状态：'+('全部首次尝试已有终态。' if done == len(rows) else '阶段未完成，以下仅为中间快照。'), '',
        '费用均值仅使用审计有效运行；可行率以所有预先登记运行为分母。标准差采用样本标准差，单样本留空。',
        '同源嵌套案例必须按 family 解释，不将种子或嵌套子集作为独立经验样本。', '',
        '- `tables/per_instance_statistics.csv`：逐实例、模型、版本和预算的费用分布及有效样本数。',
        '- `tables/paired_versions.csv`：匹配实例/模型/方法/种子/预算的新旧版本对照；双方有效时才算差异。',
        '- `tables/paired_budgets.csv`：同实例/模型/版本/种子、相同冻结输入与 K 域的预算对照；保留单侧失败，不把长预算的偶然恶化删掉。',
        '- `tables/cost_components.csv`：总费用、分量、N/Q、K 与实际配送批数。',
        '- `tables/search_diagnostics.csv`：构造和评价耗时、违规类型、各算子尝试/改进；评价有效率与移动接受数分开。',
        '- `tables/ablation_contrasts.csv`：A−B、A−C、C−D、B−D、A−D 和 A−B−C+D，并保留上下界区间。限时可行解的逆序与允许域包含关系的证书冲突分开；不将边际差相加作独立贡献。',
        '- `figures/`：实际预算使用率及外层确认的全局费用轨迹；未知最优时不称最优误差。', '']
    if pairs:
        lines += [f'配对共 {len(pairs)} 组，其中双方有效 {sum(r["both_valid"] for r in pairs)} 组；一侧失败见逐组状态。', '']
    (study/'analysis.md').write_text('\n'.join(lines), encoding='utf-8')
    return dict(planned=len(rows), terminal=done, valid=valid, paired=len(pairs))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    parser.add_argument('--no-plots', action='store_true')
    args = parser.parse_args()
    print(analyze(args.study, not args.no_plots))
