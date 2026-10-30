"""Verify cost-study artifacts, plot costs, and reprice the previous routes."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def localize(config, prior):
    """Only rewrite archived file references to their matching frozen inputs."""
    fields = ('data', 'orders', 'routing_config', 'operating_policy', 'algorithm_config',
              'distance_matrix', 'duration_matrix', 'economic_config', 'dataset_metadata')
    config = dict(config)
    for key in fields:
        value = config.get(key)
        if not isinstance(value, str):
            continue
        target = prior/'inputs'/value.replace('\\', '/').rsplit('/', 1)[-1]
        if not target.is_file():
            raise ValueError(f'Missing archived input {key}: {target}')
        config[key] = str(target)
    return config


def load_config(config, snapshot=None):
    if snapshot is not None and (snapshot/'configuration/loader.py').is_file():
        from configuration.loader import load_json
    else:
        from tools.configuration.loader import load_json
    from models.common.cycle import load_instance
    fleet = load_json(config['routing_config'])
    if 'operating_policy' in config:
        policy = config['operating_policy']
        fleet = {**fleet, 'operating_policy': load_json(policy) if isinstance(policy, str) else policy}
    return load_instance(config, fleet)


def verify(out):
    if (out/'source_snapshot/configuration/loader.py').is_file():
        from configuration.loader import load_routing_case
    else:
        from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.cost.comparison import evaluate_comparison_cost
    plan, rows = read(out/'experiment.json'), read(out/'rows.json')
    checks = []
    assert len(rows) == len(plan['jobs']) == 30
    assert {r['id'] for r in rows} == {j['id'] for j in plan['jobs']}
    for name, digest in plan['source_hashes'].items():
        assert sha(out/'source_snapshot'/name) == digest, name
    for name, digest in plan['frozen_input_hashes'].items():
        assert sha(out/name) == digest, name
    for job in plan['jobs']:
        row = next(r for r in rows if r['id'] == job['id'])
        target = out/'results'/job['id']
        if not row['valid']:
            checks.append(dict(id=job['id'], passed=False, status=row['status']))
            continue
        config, fleet = load_routing_case(job['case_path'], use_pool_config=False)
        problem = load_instance(config, fleet)
        result = read(target/'result.json')
        audit = audit_solution(problem, result)
        assert audit['valid'] and audit['periodic_replay']['valid'], (job['id'], audit.get('errors'))
        cost = evaluate_comparison_cost(problem, result, config['economic_config'], audit=audit)
        assert math.isclose(row['cost'], cost['total'], abs_tol=1e-6)
        assert cost['economic_profile_hash'] == plan['economic_profile_hash']
        domains = True
        if job['model'] >= 6 and 'count_search' in result:
            counts = result['count_search']
            domains = [c['batch_count'] for c in counts] == list(problem.batch_count_candidates)
            assert domains and len(counts) == 390
            candidates = [c for c in counts if c.get('valid')]
            assert math.isclose(min(c['objective'] for c in candidates), cost['total'], abs_tol=1e-6)
            assert result['info']['time_budget'] == 300
        elif job['model'] >= 6:
            domains = result['info']['batch_count_candidates'] == list(problem.batch_count_candidates)
            assert domains and result['info']['complete_count_domain']
            assert result['info']['solver_config']['time_limit'] == job['budget']
        write(target/'final_audit.json', audit)
        checks.append(dict(id=job['id'], passed=True, periodic_valid=True,
                           cost_recomputed=cost['total'], count_domain_verified=domains))
    payload = dict(verified_at=datetime.now(timezone(timedelta(hours=8))).isoformat(),
                   planned=len(plan['jobs']), checked=len(checks), passed=sum(c['passed'] for c in checks),
                   source_hashes_verified=len(plan['source_hashes']),
                   input_hashes_verified=len(plan['frozen_input_hashes']), checks=checks)
    write(out/'verification.json', payload)
    return payload


def reprice_prior(out, prior):
    from models.cost.joint_batch import JointCostProfile
    from models.common.cycle.audit import audit_solution
    from models.cost.comparison import evaluate_comparison_cost
    current_plan, current_rows = read(out/'experiment.json'), read(out/'rows.json')
    old_plan, old_rows = read(prior/'experiment.json'), read(prior/'rows.json')
    profile = JointCostProfile(current_plan['economic_profile'])
    comparison = []
    for old in old_rows:
        if not old.get('valid'):
            continue
        old_job = next(j for j in old_plan['jobs'] if j['id'] == old['id'])
        case_name = old_job['case_path'].replace('\\', '/').rsplit('/', 1)[-1]
        config = localize(read(prior/'cases'/case_name), prior)
        problem = load_config(config, out/'source_snapshot')
        result_path = prior/'results'/old['id']/'result.json'
        result = read(result_path)
        audit = audit_solution(problem, result)
        assert audit['valid'] and audit['periodic_replay']['valid'], (old['id'], audit.get('errors'))
        original_cost = evaluate_comparison_cost(problem, result, config['economic_config'], audit=audit)
        assert math.isclose(original_cost['total'], old['cost'], abs_tol=1e-6)
        repriced = profile.evaluate(audit, original_cost['container_pool_size'])
        current = next(r for r in current_rows if (r['family'], r['model']) == (old['family'], old['model']))
        delta = repriced['total']-current['cost'] if current['valid'] else None
        comparison.append(dict(family=old['family'], model=old['model'], old_run_id=old['id'],
            new_run_id=current['id'], old_cost_at_e1_1=old['cost'], old_cost_at_e1_0p5=repriced['total'],
            new_cost=current['cost'], new_savings_vs_same_price=delta,
            new_savings_percent=100*delta/repriced['total'] if delta is not None else None,
            old_pool=original_cost['container_pool_size'], new_pool=current.get('pool'),
            old_k=old.get('k'), new_k=current.get('k'),
            old_delivery_batches=old.get('delivery_batches'), new_delivery_batches=current.get('delivery_batches'),
            old_route_sha256=sha(result_path), old_route_reaudited=True))
    write(out/'same_price_previous_comparison.json', comparison)
    with (out/'same_price_previous_comparison.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, list(comparison[0]))
        writer.writeheader()
        writer.writerows(comparison)
    return comparison


def plot(out, rows, plan):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    families = list(dict.fromkeys(j['family'] for j in sorted(plan['jobs'], key=lambda j: j['id'])))
    colors = ['#3274A1', '#E1812C', '#3A923A', '#C03D3E']
    pieces = [('e1_distance', 'Stage 1 transport'), ('e2_distance', 'Stage 2 transport'),
              ('container_pool', 'Container pool'), ('usage', 'Wear / tracking / cleaning')]
    fig, axes = plt.subplots(2, 3, figsize=(15, 9), constrained_layout=True)
    for ax, family in zip(axes.flat, families):
        group = {r['model']: r for r in rows if r['family'] == family and r['valid']}
        x, bottom = np.arange(5), np.zeros(5)
        for (key, label), color in zip(pieces, colors):
            values = []
            for m in (3, 5, 6, 7, 8):
                parts = group.get(m, {}).get('cost_components', {})
                values.append(sum(parts.get(k, 0) for k in ('container_wear', 'tracking', 'cleaning'))
                              if key == 'usage' else parts.get(key, 0))
            ax.bar(x, values, bottom=bottom, color=color, label=label, width=.65)
            bottom += values
        ax.set_xticks(x, ['M3', 'M5', 'M6', 'M7', 'M8'])
        ax.set_title(family.replace('zhou_Set2_', '').replace('_subset50', ' (50 customers)')
                     + ('' if 'subset50' in family else ' (100 customers)'), fontsize=11)
        ax.set_ylabel('Total cost (synthetic units)')
        ax.set_ylim(0, max(bottom)*1.18 if max(bottom) else 1)
        ax.grid(axis='y', alpha=.2)
        ax.set_axisbelow(True)
        for i, m in enumerate((3, 5, 6, 7, 8)):
            if m in group:
                r = group[m]
                ax.text(i, bottom[i]+max(bottom)*.02, f'{bottom[i]:,.0f}\nK={r["k"]}, N={r["pool"]}',
                        ha='center', va='bottom', fontsize=8)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='outside lower center', ncol=4, frameon=False)
    fig.suptitle('E1 distance rate 0.5 | ALNS budget cap 300 s | seed 42', fontsize=16)
    fig.savefig(out/'cost_components.png', dpi=160)
    fig.savefig(out/'cost_components.svg')
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--prior', default='output/model35678_comparison/20261006_pool25_truck1_autoK_5min_v1')
    args = parser.parse_args()
    out, prior = Path(args.output).resolve(), Path(args.prior).resolve()
    sys.path.insert(0, str(out/'source_snapshot'))
    verified = verify(out)
    comparison = reprice_prior(out, prior)
    rows, plan = read(out/'rows.json'), read(out/'experiment.json')
    plot(out, rows, plan)
    report = (out/'report.md').read_text(encoding='utf-8')
    utilization = []
    for row in rows:
        item = dict(id=row['id'], family=row['family'], model=row['model'],
                    solve_seconds=row.get('solve_wall_seconds'), budget_seconds=row['budget'],
                    utilization_percent=100*row.get('solve_wall_seconds', 0)/row['budget'])
        if row['model'] >= 6 and (out/'results'/row['id']/'count_search.json').exists():
            counts = read(out/'results'/row['id']/'count_search.json')
            item.update(first_k_budget=counts[0]['budget'],
                        first_k_runtime=counts[0].get('runtime'),
                        valid_candidate_runtime=sum(c.get('runtime') or 0 for c in counts if c.get('valid')),
                        unrun_count=row.get('unrun_count'),
                        proved_infeasible_count=row.get('proved_infeasible_count'))
        utilization.append(item)
    write(out/'budget_utilization.json', utilization)
    report += '\n\n## 搜索预算利用率\n\n'
    report += ('300秒是一次调用的预算上限。当前实现按尚未遍历的K均分剩余时间，起初每个K约获得300/390=0.77秒。'
               '若后段大量K被快速证明不可行，遍历结束后不会重新搜索前面的K，因此可能留下大量未使用预算。'
               '这会削弱本轮的搜索质量；返回K=1或成本更高不能据此解释为多批次机制无效。\n\n'
               '| 模型 | 完成组数 | 平均求解秒数 | 最短秒数 | 最长秒数 | 平均预算利用率 |\n'
               '|---|---:|---:|---:|---:|---:|\n')
    for model in (3, 5, 6, 7, 8):
        group = [r for r in utilization if r['model'] == model and r['solve_seconds'] is not None]
        if group:
            durations = [r['solve_seconds'] for r in group]
            report += (f'| M{model} | {len(group)} | {statistics.mean(durations):.2f} | '
                       f'{min(durations):.2f} | {max(durations):.2f} | '
                       f'{statistics.mean(r["utilization_percent"] for r in group):.1f}% |\n')
    report += '\n本轮保持冻结算法完整执行，预算分配策略未在实验中途修改。未使用时间和未运行K均原样保留，不能作为最优性证明。\n'
    report += '\n\n## 成本组成\n\n![成本组成](cost_components.png)\n'
    report += '\n## 上一轮方案按同一0.5价格重计\n\n'
    report += ('上一轮30条路线重新独立审计后，使用本轮价格计价。下表正数表示本轮找到的方案费用更低，负数表示本轮搜索结果更差。'
               '两轮的K允许域、代码版本、机器和预算分配不同，不能视为严格算法性能对照。\n\n'
               '| 算例 | 模型 | 旧方案按0.5计价 | 新方案成本 | 新方案费用降幅 | 旧K→新K |\n'
               '|---|---:|---:|---:|---:|---|\n')
    for r in sorted(comparison, key=lambda r: (r['family'], r['model'])):
        new = f'{r["new_cost"]:.2f}' if r['new_cost'] is not None else '—'
        change = f'{r["new_savings_percent"]:+.2f}%' if r['new_savings_percent'] is not None else '—'
        report += (f'| {r["family"]} | M{r["model"]} | {r["old_cost_at_e1_0p5"]:.2f} | {new} | '
                   f'{change} | {r["old_k"]}→{r["new_k"]} |\n')
    improved = sum(r['new_savings_vs_same_price'] is not None and r['new_savings_vs_same_price'] > 1e-6 for r in comparison)
    worse = sum(r['new_savings_vs_same_price'] is not None and r['new_savings_vs_same_price'] < -1e-6 for r in comparison)
    report += f'\n同价比较中，本轮{improved}组更低、{worse}组更高。最终复核{verified["passed"]}/{verified["checked"]}通过。\n'
    (out/'report.md').write_text(report, encoding='utf-8')
    write(out/'analysis_metadata.json', dict(script=str(Path(__file__).relative_to(ROOT)), script_sha256=sha(__file__),
          prior=str(prior), prior_reaudited=len(comparison), improved=improved, worse=worse,
          current_verified=verified['passed'], statuses=dict(Counter(r['status'] for r in rows))))
    print(json.dumps(dict(verified=verified['passed'], old_routes_reaudited=len(comparison),
                         improved=improved, worse=worse), ensure_ascii=False))


if __name__ == '__main__':
    main()
