"""Replay all saved routes, verify frozen bytes, and report the new objective.

Case path rebasing permits verification after cloning into another directory.
Original cases and solution files are never rewritten by this verifier.
"""
from __future__ import annotations

from collections import defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
PRIOR = OUT.parent/'20261006_5min_v1'
MODELS = (3, 5, 6, 7, 8)
ZERO_COMPONENTS = ('e1_work', 'e2_work', 'e1_vehicles', 'e2_vehicles', 'e1_trips', 'e2_trips')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rebased(value, original_root):
    if isinstance(value, dict):
        return {key: rebased(item, original_root) for key, item in value.items()}
    if isinstance(value, list):
        return [rebased(item, original_root) for item in value]
    if isinstance(value, str):
        normalized = value.replace('\\', '/')
        if normalized.startswith(original_root+'/'):
            return str(ROOT/normalized[len(original_root)+1:])
    return value


def main():
    plan, rows, summary = [read(OUT/name) for name in ('experiment.json', 'rows.json', 'summary.json')]
    assert len(rows) == len(plan['jobs']) == 30
    assert len({row['id'] for row in rows}) == 30
    assert {row['id'] for row in rows} == {job['id'] for job in plan['jobs']}
    assert sha(OUT/'run_study.py') == plan['runner_sha256']
    assert sha(OUT/'inputs/common_economic.json') == plan['common_economic_sha256']
    for name, digest in plan['source_hashes'].items():
        assert sha(OUT/'source_snapshot'/name.replace('\\', '/')) == digest, name
    for record in plan['input_hashes'].values():
        assert sha(OUT/record['snapshot']) == record.get('snapshot_sha256', record['sha256'])
    sys.path.insert(0, str(OUT/'source_snapshot'))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle.instance import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.cost.comparison import evaluate_comparison_cost
    from models.cost.joint_batch import JointCostProfile
    profile = JointCostProfile(read(OUT/'inputs/common_economic.json'))
    assert profile.hash == plan['economic_profile_hash']
    assert all(profile.rates[key] == 0 for key in plan['zero_cost_fields'])
    original_root = plan['jobs'][0]['case_path'].replace('\\', '/').split('/output/model35678_comparison/')[0]
    comparisons, diagnostics, count_candidates, replay_checks = [], [], [], []
    old_rows = read(PRIOR/'reprice_distance_pool_v1/rows.json')
    jobs = {job['id']: job for job in plan['jobs']}
    for row in sorted(rows, key=lambda item: item['id']):
        job = jobs[row['id']]
        target = OUT/'results'/row['id']
        if not row['valid']:
            replay_checks.append(dict(id=row['id'], valid=False, status=row['status'], error=row.get('error')))
            continue
        case_path = OUT/'cases'/Path(job['case_path'].replace('\\', '/')).name
        config = rebased(read(case_path), original_root)
        with tempfile.TemporaryDirectory(prefix='m35678_replay_') as tmp:
            temporary_case = Path(tmp)/'case.json'
            write(temporary_case, config)
            config, fleet = load_routing_case(temporary_case, use_pool_config=False)
            problem = load_instance(config, fleet)
        result, saved_audit, saved_cost = [read(target/name) for name in
            ('result.json', 'independent_audit.json', 'comparison_cost.json')]
        audit = audit_solution(problem, result)
        assert audit['valid'] and audit['periodic_replay']['valid'], (row['id'], audit['errors'])
        assert audit['periodic_replay']['cycles'] == 2
        assert saved_audit['valid'] and saved_audit['periodic_replay']['valid']
        cost = evaluate_comparison_cost(problem, result, config['economic_config'], audit=audit)
        assert cost['economic_profile_hash'] == saved_cost['economic_profile_hash'] == profile.hash
        assert cost['components'].keys() == saved_cost['components'].keys()
        assert all(math.isclose(value, saved_cost['components'][name], abs_tol=1e-6, rel_tol=1e-9)
                   for name, value in cost['components'].items())
        assert all(cost['components'][name] == 0 for name in ZERO_COMPONENTS)
        assert math.isclose(cost['total'], row['cost'], abs_tol=1e-6)
        assert math.isclose(cost['total'], saved_cost['total'], abs_tol=1e-6)
        if row['model'] in (6, 7, 8):
            assert math.isclose(cost['total'], result['info']['objective'], abs_tol=1e-5, rel_tol=1e-7)
            assert row['pool'] == audit['minimum_pool_for_schedule']
        returned = sum(release['quantity'] for release in audit['depot_releases'])
        picked = sum(event['quantity'] for trip in result['e1_trips'] for event in trip['events'] if event['kind'] == 'p')
        assert returned == picked == audit['container_balance']['returned_to_depot'] == problem.total
        assert audit['container_balance']['issued'] == problem.total
        assert math.isclose(cost['components']['cleaning'], returned*profile.rates['cleaning_cost_per_return'], abs_tol=1e-8)
        pool = cost['container_pool_size']
        assert math.isclose(cost['components']['container_pool'], pool*5, abs_tol=1e-8)
        assert math.isclose(cost['total'], sum(audit['distances'].values())+5*pool+0.5*problem.total, abs_tol=1e-6)
        calendar = result['operating_policy']
        assert calendar['cycle_start'] == problem.operating_policy.cycle_start
        assert calendar['cycle_end'] == problem.operating_policy.cycle_end
        assert all(release['clean_ready'] <= calendar['cycle_end'] for release in audit['depot_releases'])
        assert audit['terminal_state']['clean'] == pool
        assert all(quantity == 0 for state, quantity in audit['terminal_state'].items() if state != 'clean')
        demands = {batch['id']: batch['container_demand'] for batch in result['batches']}
        actual_batches = sum(quantity > 0 for quantity in demands.values())
        assert actual_batches == row['delivery_batches']
        returned_by_batch = defaultdict(int)
        for trip in result['e1_trips']:
            returned_by_batch[trip['batch_id']] += sum(event['quantity'] for event in trip['events'] if event['kind'] == 'p')
        diagnostics.append(dict(id=row['id'], family=row['family'], model=row['model'],
            configured_or_optimized_pool=pool, minimum_pool=audit['minimum_pool_for_schedule'],
            total_box_uses=problem.total, cycle_batch_count=len(calendar['batches']),
            actual_delivery_batches=actual_batches, demand_by_batch=demands,
            returned_by_batch={batch['id']: returned_by_batch[batch['id']] for batch in result['batches']},
            cleaning_cost=cost['components']['cleaning'], calendar=calendar['batches'],
            last_depot_unload=max(release['unload_complete'] for release in audit['depot_releases']),
            last_clean_ready=max(release['clean_ready'] for release in audit['depot_releases']),
            cycle_start=calendar['cycle_start'], cycle_end=calendar['cycle_end'],
            terminal_state=audit['terminal_state'], periodic_replay=audit['periodic_replay']))
        for candidate in result.get('count_search', []):
            count_candidates.append(dict(id=row['id'], family=row['family'], model=row['model'],
                demand=problem.total, selected_cycle_k=row['k'], **candidate))
        old = next(item for item in old_rows if item['id'] == row['id'])
        assert old['economic_profile_hash'] == profile.hash
        comparisons.append(dict(id=row['id'], family=row['family'], model=row['model'],
            old_route_new_prices=old['revised_cost'], fresh_search_cost=row['cost'],
            reduction_percent=(old['revised_cost']-row['cost'])/old['revised_cost']*100,
            old_pool=old['pool'], new_pool=pool))
        replay_checks.append(dict(id=row['id'], valid=True, repeated_cycles=2,
            old_audit_valid=saved_audit['valid'], cost_matches_objective=row['model'] in (6, 7, 8),
            zero_components=list(ZERO_COMPONENTS), returned_box_uses=returned,
            formula_verified='distance + 5N + 0.5Q'))
    assert len(replay_checks) == 30
    write(OUT/'verification.json', dict(checked=30, valid=sum(item['valid'] for item in replay_checks),
        frozen_hashes_valid=True, economic_profile_hash=profile.hash, checks=replay_checks))
    write(OUT/'pool_cycle_cleaning_diagnostics.json', diagnostics)
    write(OUT/'count_candidates.json', count_candidates)
    write(OUT/'same_price_search_comparison.json', comparisons)
    if comparisons:
        with (OUT/'same_price_search_comparison.csv').open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(comparisons[0]))
            writer.writeheader()
            writer.writerows(comparisons)
    write_report(plan, rows, summary, comparisons, diagnostics, count_candidates)
    plot_costs(summary)
    print(f'VERIFIED {sum(item["valid"] for item in replay_checks)}/30 routes, costs, frozen bytes and two-cycle replay')


def write_report(plan, rows, summary, comparisons, diagnostics, count_candidates):
    fmt = lambda value: '—' if value is None else f'{value:,.2f}'
    winners = []
    for case in summary['cases']:
        feasible = [row for row in case['selected'].values() if row]
        if feasible:
            best = min(feasible, key=lambda row: row['cost'])
            winners.append(f'{case["customers"]}点 {case["family"]}: M{best["model"]}')
    joint_diagnostics = [item for item in diagnostics if item['model'] in (6, 7, 8)]
    single_delivery_count = sum(item['actual_delivery_batches'] == 1 for item in joint_diagnostics)
    full_demand_pool_count = sum(item['configured_or_optimized_pool'] == item['total_box_uses']
                                for item in joint_diagnostics)
    lines = ['# 新成本口径：模型 3、5、6、7、8 大规模重新优化', '',
        '本轮使用之前的六个大规模算例，重新运行全部五个模型。一级和二级运输的占用时间费、每辆车固定费、每趟固定费均为零；其余成本和物理条件保持原值。', '',
        f'共 {summary["completed"]}/{summary["planned"]} 次运行，{summary["valid"]} 次通过独立连续双周期审计。每次 ALNS 300 秒、seed=42、最多 1000000 次迭代、四进程；模型 6–8 在 K=1/2/3 之间共享 300 秒。', '',
        '本轮返回方案中，各例最低成本模型为：'+'；'.join(winners)+'。', '',
        f'最终联合模型方案中，{single_delivery_count}/{len(joint_diagnostics)} 个只有一次实际配送，{full_demand_pool_count}/{len(joint_diagnostics)} 个箱池等于周期总需求。费用差额由运输距离和箱池数量决定，不包含取消费用本身的收益。', '',
        f'成本参数哈希：`{plan["economic_profile_hash"]}`。所有有效方案都在新价格下重新求解；模型 3、5保留原距离目标，再统一计价，模型 6–8直接优化新周期成本。', '',
        '## 保留的成本和约束', '',
        '距离单价为 1，箱池费为每箱每周期 5，清洗费为每次实际回总仓的箱次 0.2，追踪费为每次发出的箱次 0.1，磨损费为每箱次 10/50=0.2。因所有需求箱次均完整回库，当前每周期总费用为：', '',
        '`C = 一级距离 + 二级距离 + 5 × 箱池数量 N + 0.5 × 需求箱次 Q`。', '',
        '未使用的干净箱子不收清洗费；同一个物理箱子再次使用、回库，需要再次清洗并计费；最后一轮回库的清洗也计入本周期。清洗费由实际一级回库数量累计核验，不能用独立箱子数 N 代替，也不能用 K×N。', '',
        '配送/回收时间窗、使用时长、车队和装载容量、分界约束、清洗时长均仍生效。周期保持 08:00–21:00（780分钟）；周期末所有箱子已回总仓且洗净，车辆可继续下一周期。箱体初始投入在成本文件单列，不并入运营成本。', '',
        '## 六例周期总成本', '',
        '| 算例 | 点数 | Q | M3 | M5 | M6 | M7 | M8 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for case in summary['cases']:
        selected = case['selected']
        lines.append(f'| {case["family"]} | {case["customers"]} | {case["demand"]} | '
            + ' | '.join(fmt(selected[str(model)]['cost'] if selected[str(model)] else None) for model in MODELS)+' |')
    findings = []
    lines += ['', '## 相对单批次基准的费用差额', '',
        '费用差额采用本轮 M3 与本轮最低成本联合模型配对。正数表示联合模型节省，负数表示本次搜索的联合模型成本更高。清洗、追踪和磨损总费用相同，差额只来自运输距离和箱池数量。', '',
        '| 算例 | 最低联合模型 | 相对 M3 降低比例 | 距离费用节省 | 箱池费用节省 | 总费用节省 |',
        '|---|---|---:|---:|---:|---:|']
    for case in summary['cases']:
        selected = case['selected']
        baseline = selected['3']
        joint = [selected[str(model)] for model in (6, 7, 8) if selected[str(model)]]
        if not baseline or not joint:
            continue
        best = min(joint, key=lambda row: row['cost'])
        distance_saving = baseline['distance']-best['distance']
        pool_saving = (baseline['pool']-best['pool'])*5
        saving = baseline['cost']-best['cost']
        assert math.isclose(saving, distance_saving+pool_saving, abs_tol=1e-6)
        percent = saving/baseline['cost']*100
        findings.append(dict(family=case['family'], best_joint_model=best['model'],
            m3_cost=baseline['cost'], best_joint_cost=best['cost'],
            saving_percent=percent, distance_saving=distance_saving,
            pool_saving=pool_saving, total_saving=saving))
        lines.append(f'| {case["family"]} | M{best["model"]} | {percent:.2f}% | {fmt(distance_saving)} | {fmt(pool_saving)} | {fmt(saving)} |')
    selected_joint = [row for row in rows if row['valid'] and row['model'] in (6, 7, 8)]
    demand_by_family = {case['family']: case['demand'] for case in summary['cases']}
    reduced = [row['id'] for row in selected_joint if row['pool'] < demand_by_family[row['family']]]
    reduced_candidates = [item for item in count_candidates if item.get('valid')
        and item.get('container_pool_size', item['demand']) < item['demand']]
    empty_segments = [row['id'] for row in selected_joint if row['k'] > row['delivery_batches']]
    write(OUT/'findings.json', dict(per_case=findings,
        selected_joint_solutions=len(selected_joint), selected_pool_below_total_demand=reduced,
        solver_valid_candidates_pool_below_total_demand=len(reduced_candidates),
        selected_calendars_with_empty_segments=empty_segments))
    lines += ['', f'最终联合模型方案中，{len(reduced)}/{len(selected_joint)} 个箱池小于周期需求箱次 Q；搜索候选摘要中有 {len(reduced_candidates)} 个求解器审计有效的候选箱池小于 Q。是否选择多批次取决于箱池节省能否抵消新增运输距离。',
        '', f'有 {len(empty_segments)} 个最终联合方案含空日历段；实际配送批次见下表，不能仅凭 K 判断循环利用。']
    lines += ['', '![新成本组成](cost_components.png)', '', '## 箱池数量、批次和回收', '',
        '模型 3、5的箱池仍为输入配置值；另列原时间表最低需箱量供参考。模型 6–8的费用采用优化输出箱池，独立审计核对其等于该时间表最低需箱量。K 是日历段数，实际配送批次只计有需求的段；空段不表示容器循环利用。', '',
        '| 算例 | 模型 | 使用箱池 N | 时间表最低需箱 | 日历 K / 实际配送批次 | 分批回总仓箱次 | 清洗费 | 求解秒数 |',
        '|---|---|---:|---:|---|---|---:|---:|']
    by_id = {item['id']: item for item in diagnostics}
    for row in sorted(rows, key=lambda item: item['id']):
        if row['valid']:
            diag = by_id[row['id']]
            returns = ', '.join(f'{key}:{value}' for key, value in diag['returned_by_batch'].items())
            lines.append(f'| {row["family"]} | M{row["model"]} | {row["pool"]} | {row["minimum_pool"]} | {row["k"]}/{row["delivery_batches"]} | {returns} | {fmt(diag["cleaning_cost"])} | {fmt(row["solve_wall_seconds"])} |')
        else:
            lines.append(f'| {row["family"]} | M{row["model"]} | — | — | — | {row["status"]} | — | — |')
    lines += ['', '## 同一新价格：旧方案重新计价与本轮重新搜索', '',
        '此表两列都使用当前新价格，因此差额仅表示搜索得到的方案变化，不把取消费用本身当作优化收益。有限预算和并发机器负载会改变搜索轨迹，同种子也不保证本轮每次都更好。', '',
        '| 算例 | 模型 | 旧方案按新价格 | 本轮重新搜索 | 成本降低比例 | 旧池→新池 |',
        '|---|---|---:|---:|---:|---|']
    for item in comparisons:
        lines.append(f'| {item["family"]} | M{item["model"]} | {fmt(item["old_route_new_prices"])} | {fmt(item["fresh_search_cost"])} | {item["reduction_percent"]:.2f}% | {item["old_pool"]}→{item["new_pool"]} |')
    lines += ['', '## M3、M5原时间表的最低箱池参考', '',
        '下表仅将原配置池替换为审计给出的该方案最低需箱数，路线、时刻和批次不变。这是重新计价参考，不是模型 3、5箱池优化输出。', '',
        '| 算例 | M3当前 | M3最低池参考 | M5当前 | M5最低池参考 |',
        '|---|---:|---:|---:|---:|']
    for case in summary['cases']:
        values = []
        for model in (3, 5):
            row = case['selected'][str(model)]
            values += [row['cost'] if row else None, row['minimum_pool_reference_cost'] if row else None]
        lines.append(f'| {case["family"]} | '+' | '.join(fmt(value) for value in values)+' |')
    lines += ['', '## 搜索中各 K 的候选记录', '',
        '这里保留求解器报告的每个 K 的最佳候选（count_search）；独立双周期审计针对最终返回的 30 个方案，不把候选摘要的 valid 字段当成额外独立审计。', '',
        '| 算例 | 模型 | K | 候选箱池 | 候选成本 | 求解器审计 | 最终 K |',
        '|---|---|---:|---:|---:|---|---:|']
    for item in count_candidates:
        lines.append(f'| {item["family"]} | M{item["model"]} | {item["batch_count"]} | {item.get("container_pool_size", "—")} | {fmt(item.get("objective"))} | {item.get("valid", False)} | {item["selected_cycle_k"]} |')
    lines += ['', '## 范围和复现', '',
        '- 本轮为单种子、有限预算可行解，没有全局最优或随机稳定性证明。五个模型的可行域和优化目标仍有区别，不能把全部成本差额归因于多批次机制。',
        '- 工时费为零后，移动等待时间本身不再直接降低成本；分界仍会影响可行性、路线及箱子复用。',
        '- 若联合模型成本高于 M3，这说明本次搜索返回较差方案，不能据此推断其理论最优值更差。K=1 时模型 6–8的日历可行域相同，启发式返回值仍可能不同。',
        '- 模型 3把全部 300 秒用于单批次路线搜索；联合模型把总预算分给三个 K，每个 K 的实际预算及迭代量保存在 count_candidates.json。即使最终都选择单批次，搜索路线、算子和分配到 K=1 的时间仍有差异。',
        '- 三张底图各包含 50 点子集和 100 点完整版，样本不是六个独立总体样本。',
        '- 成本为 synthetic_units 合成单位，未标定为人民币；300 秒为软截止，四进程共享机器。',
        '- 输入和代码均冻结并校验原始字节哈希；每个最终方案再次重放并核对新目标、零费用项、优化箱池、实际清洗箱次和连续两周期可复用性。', '',
        '复核已有结果：`python output/model35678_comparison/20261006_distance_pool_5min_v1/verify_and_report.py`。脚本支持仓库路径变化，临时重定位案例路径，不改写归档输入。', '',
        '重新执行搜索：把 run_study.py、monitor_study.py、verify_and_report.py 复制到 output/model35678_comparison/ 下一个新的空目录，先运行该目录的 run_study.py，再运行 verify_and_report.py。已注册或已完成的目录拒绝覆盖。', '',
        '文件：experiment.json 为预注册计划；runs.csv 为全部运行；results/ 为方案、独立审计、成本；verification.json 为复核；pool_cycle_cleaning_diagnostics.json 为箱池/日历/清洗明细；count_candidates.json 为按 K 搜索摘要；same_price_search_comparison.csv 为同价旧方案与新搜索配对。']
    (OUT/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


def plot_costs(summary):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10,
        'axes.spines.top': False, 'axes.spines.right': False})
    groups = [('Container pool', '#3b6ea8', ['container_pool']),
              ('Distance', '#e99d3c', ['e1_distance', 'e2_distance']),
              ('Cleaning / tracking / wear', '#a0a7b0', ['cleaning', 'tracking', 'container_wear'])]
    fig, axes = plt.subplots(2, 3, figsize=(16, 10))
    for ax, index in zip(axes.flat, [0, 2, 4, 1, 3, 5]):
        case = summary['cases'][index]
        selected, x, bottoms = case['selected'], np.arange(5), np.zeros(5)
        for label, color, keys in groups:
            values = [sum(selected[str(model)]['cost_components'][key] for key in keys)
                      if selected[str(model)] else 0 for model in MODELS]
            ax.bar(x, values, bottom=bottoms, color=color, label=label, width=.65)
            bottoms += np.array(values)
        for position, total in enumerate(bottoms):
            ax.annotate(f'{total:,.1f}' if total else 'No solution', (position, total),
                xytext=(0, 5), textcoords='offset points', ha='center', fontsize=9)
        title = case['family'].replace('zhou_', '').replace('_subset50', '')
        ax.set_title(f'{title}\n{case["customers"]} customers; Q={case["demand"]}')
        ax.set_xticks(x, [f'M{model}' for model in MODELS])
        ax.set_ylabel('Synthetic cost units per cycle')
        ax.set_ylim(0, max(bottoms)*1.18 if max(bottoms) else 1)
        ax.grid(axis='y', alpha=.18)
        ax.set_axisbelow(True)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='lower center', bbox_to_anchor=(.5, .055), ncol=3, frameon=False)
    fig.suptitle('Fresh search with work, vehicle and trip charges excluded', fontsize=16)
    fig.text(.5, .015, 'Six large cases; 300 seconds; seed 42. M3/M5 distance objective; M6-M8 new cost objective.',
        ha='center', fontsize=10, color='#555555')
    fig.tight_layout(rect=(0, .12, 1, .95))
    fig.savefig(OUT/'cost_components.png', dpi=180)
    fig.savefig(OUT/'cost_components.svg')
    plt.close(fig)


if __name__ == '__main__':
    main()
