"""Report actual delivery batches and optimized stock for the new price study."""
from __future__ import annotations

from collections import Counter
import json
import math

MODELS = (3, 5, 6, 7, 8)


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def render_report(out, plan, rows, summary, comparisons, diagnostics, candidates):
    fmt = lambda value: '—' if value is None else f'{value:,.2f}'
    joint = [item for item in diagnostics if item['model'] in (6, 7, 8)]
    single = [item['id'] for item in joint if item['actual_delivery_batches'] == 1]
    multiple = [item['id'] for item in joint if item['actual_delivery_batches'] > 1]
    smaller = [item['id'] for item in joint if item['configured_or_optimized_pool'] < item['total_box_uses']]
    empty = [item['id'] for item in joint if item['cycle_batch_count'] > item['actual_delivery_batches']]
    reduced_candidates = [item for item in candidates if item.get('valid')
        and item.get('container_pool_size', item['demand']) < item['demand']]
    tradeoffs = []
    for identifier in sorted({item['id'] for item in candidates}):
        group = [item for item in candidates if item['id'] == identifier and item.get('valid')]
        baseline = next((item for item in group if item['batch_count'] == 1), None)
        reduced = [item for item in group if baseline
            and item['container_pool_size'] < baseline['container_pool_size']]
        if not reduced:
            continue
        candidate = min(reduced, key=lambda item:item['objective'])
        pool_saving = 15*(baseline['container_pool_size']-candidate['container_pool_size'])
        net_increase = candidate['objective']-baseline['objective']
        # The complete-demand use/return terms cancel within the same case.
        baseline_transport = baseline['objective']-15*baseline['container_pool_size']-0.6*baseline['demand']
        candidate_transport = candidate['objective']-15*candidate['container_pool_size']-0.6*candidate['demand']
        transport_increase = candidate_transport-baseline_transport
        assert math.isclose(net_increase, transport_increase-pool_saving, abs_tol=1e-6)
        tradeoffs.append(dict(id=identifier, family=candidate['family'], model=candidate['model'],
            candidate_k=candidate['batch_count'], baseline_pool=baseline['container_pool_size'],
            candidate_pool=candidate['container_pool_size'], baseline_cost=baseline['objective'],
            candidate_cost=candidate['objective'], pool_cost_saving=pool_saving,
            weighted_transport_increase=transport_increase, total_cost_increase=net_increase,
            audit_scope='solver-internal candidate summary; final routes independently audited'))
    findings = dict(selected_joint_solutions=len(joint), single_actual_delivery=single,
        multiple_actual_deliveries=multiple, selected_pool_below_total_demand=smaller,
        empty_calendar_segments=empty, solver_valid_candidates_below_Q=len(reduced_candidates),
        reduced_candidate_tradeoffs=tradeoffs,
        counts_by_model={str(model):dict(Counter(str(item['actual_delivery_batches'])
            for item in joint if item['model'] == model)) for model in (6, 7, 8)}, per_case=[])
    lines = ['# 箱池成本15：六例五分钟重新优化', '',
        '## Material Passport', '',
        '- Origin Skill: academic-research-suite / experiment-agent',
        '- Origin Mode: run (continuous)',
        '- Origin Date: 2026-10-06',
        '- Verification Status: ' + ('VERIFIED' if summary['valid'] == summary['planned'] else 'PARTIAL'),
        '- Version Label: truck_tricycle_pool15_5min_v1', '',
        f'远程基准提交：`{plan["commit"]}`。本轮按用户要求将箱池系数调为15、磨损调为15/50=0.3，清洗沿用0.2，重新搜索，共 {summary["completed"]}/{summary["planned"]} 次，独立双周期审计通过 {summary["valid"]} 次。', '',
        f'在最终模型6–8方案中，{len(single)}/{len(joint)} 个只有一次实际配送，{len(multiple)}/{len(joint)} 个有多次实际配送，{len(smaller)}/{len(joint)} 个箱池小于总需求箱次 Q。日历有空段的方案为 {len(empty)} 个，不能仅凭日历 K 判断循环利用。', '',
        '六个原大规模算例、模型3/5/6/7/8，每次 ALNS 300 秒、seed=42、最多1000000次迭代、四进程。模型6–8在K=1/2/3之间共享总预算。订单、分仓、道路、时间窗、车队、容量、清洗时长和周期边界均与上一轮相同。', '',
        '## 本轮参数', '',
        '相对上一轮，仅改变箱池单价5→15和磨损参考箱价5→15（寿命仍50次）。清洗0.2、追踪0.1、两级距离系数、原始距离与速度均沿用。', '',
        '一级距离系数2.535，二级0.05647058823529412，箱池系数15。一级/二级占用时间、车辆固定和趟次固定费均为0。清洗0.2/实际回总仓箱次；追踪0.1/实际发箱次；磨损按箱价参考15、寿命50次，为0.3/发箱次。', '',
        '`C = 2.535D1 + 0.056470588D2 + 15N + 0.4U + 0.2R`；完整发出、回收时 U=R=Q，得到 `C = 2.535D1 + 0.056470588D2 + 15N + 0.6Q`。', '',
        '两级运输不能再直接将原始距离相加计费。价格和距离仍是合成比例测试，未校准为实际人民币运营支出。初始购箱参考15N在成本文件单列，不再次加入周期总费用。', '',
        f'主参数语义哈希：`{plan["economic_profile_hash"]}`；源文件 `{plan["primary_profile_source"]}` 已复制到 inputs/common_economic.json。', '',
        '模型3/5保留原始距离目标和配置箱池，再统一加权计价；模型6–8直接优化本轮费用并使用优化箱池。原始距离目标与加权费用目标不同，不能把全部收益解释为分批机制。', '',
        '## 最终方案成本', '',
        '| 算例 | 点数 | Q | M3 | M5 | M6 | M7 | M8 | 最低成本模型 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---|']
    for case in summary['cases']:
        selected = case['selected']
        feasible = [item for item in selected.values() if item]
        best = min(feasible, key=lambda item:item['cost']) if feasible else None
        best_label = f'M{best["model"]}' if best else '—'
        values = [fmt(selected[str(model)]['cost'] if selected[str(model)] else None) for model in MODELS]
        lines.append(f'| {case["family"]} | {case["customers"]} | {case["demand"]} | '
            + ' | '.join(values)+f' | {best_label} |')
    winners = Counter()
    for case in summary['cases']:
        feasible = [item for item in case['selected'].values() if item]
        if feasible:
            winners[str(min(feasible, key=lambda item:item['cost'])['model'])] += 1
    findings['lowest_cost_counts_by_model'] = dict(winners)
    winner_text = '、'.join(f'M{model}在{count}例最低' for model,count in sorted(winners.items()))
    lines += ['', f'本轮有限搜索结果：{winner_text}。模型3/5和联合模型的搜索目标、预算分配不同，这一排名不能作为各模型理论最优成本的排序。', '',
        '![费用组成](cost_components.png)', '', '## 实际配送批次、箱池和清洗', '',
        'N 是独立物理箱池，Q 是本周期需求箱次。模型3/5的N来自输入配置；模型6–8的N来自优化输出，并经独立库存重放核对。实际配送批次只计有需求的日历段，空段不代表箱子再次使用。', '',
        '| 算例 | 模型 | N | 原时间表最低需箱 | Q | 日历K / 实际配送批次 | 各批发箱 | 各批回总仓 | 清洗费 |',
        '|---|---|---:|---:|---:|---|---|---|---:|']
    by_id = {item['id']:item for item in diagnostics}
    for row in sorted(rows, key=lambda item:item['id']):
        if not row['valid']:
            lines.append(f'| {row["family"]} | M{row["model"]} | — | — | — | {row["status"]} | — | — | — |')
            continue
        diag = by_id[row['id']]
        issues = ', '.join(f'{key}:{value}' for key,value in diag['demand_by_batch'].items())
        returns = ', '.join(f'{key}:{value}' for key,value in diag['returned_by_batch'].items())
        lines.append(f'| {row["family"]} | M{row["model"]} | {row["pool"]} | {row["minimum_pool"]} | {diag["total_box_uses"]} | {row["k"]}/{row["delivery_batches"]} | {issues} | {returns} | {fmt(diag["cleaning_cost"])} |')
    lines += ['', '所有箱子在08:00–21:00的同一780分钟周期末回到总仓并洗净。清洗按每次实际回库箱次累计，包含最后一轮；同一箱子复用后再次回库再次收费。连续两个周期均已验证。', '',
        '## 各K候选与箱池节省', '',
        f'求解器候选摘要中，有 {len(reduced_candidates)} 个有效候选的箱池小于Q。候选 valid 来自求解器内部审计；独立双周期复核仅针对30个最终返回方案。', '',
        '| 算例 | 模型 | 候选K | 候选N | 候选成本 | 状态 | 最终K |',
        '|---|---|---:|---:|---:|---|---:|']
    for item in candidates:
        lines.append(f'| {item["family"]} | M{item["model"]} | {item["batch_count"]} | {item.get("container_pool_size", "—")} | {fmt(item.get("objective"))} | {item["status"]} | {item["selected_cycle_k"]} |')
    lines += ['', '固定两条完整回收方案之间，多批次要降低本轮周期成本，必须满足 `2.535ΔD1 + 0.056470588ΔD2 < 15×节省箱数`。清洗、追踪和按使用磨损在Q相同时相同。候选摘要不保存原始路线，不能从一个加权总成本反推分别的一级、二级距离。', '',
        '下表对每个模型运行，取成本最低的减箱候选，与该运行的K=1候选比较。运输增量由加权总目标减去箱池费和固定箱次费得到；正的总增量表示减箱后仍更贵。两者均为有限搜索找到的候选，不能据此证明多批次的理论最优成本。', '',
        '| 算例 | 模型 | 减箱候选K | 单批池→候选池 | 箱池费节省 | 加权运输增量 | 总成本增量 |',
        '|---|---|---:|---|---:|---:|---:|']
    for item in tradeoffs:
        lines.append(f'| {item["family"]} | M{item["model"]} | {item["candidate_k"]} | {item["baseline_pool"]}→{item["candidate_pool"]} | {fmt(item["pool_cost_saving"])} | {fmt(item["weighted_transport_increase"])} | {fmt(item["total_cost_increase"])} |')
    lines += ['',
        '## 本轮最低联合方案与M3的差额', '',
        '| 算例 | 最低联合模型 | 相对M3降低比例 | 加权运输节省 | 箱池节省 | 总节省 |',
        '|---|---|---:|---:|---:|---:|']
    for case in summary['cases']:
        selected = case['selected']
        baseline = selected['3']
        feasible = [selected[str(model)] for model in (6,7,8) if selected[str(model)]]
        if not baseline or not feasible:
            continue
        best = min(feasible, key=lambda item:item['cost'])
        c0, c1 = baseline['cost_components'], best['cost_components']
        distance_saving = c0['e1_distance']+c0['e2_distance']-c1['e1_distance']-c1['e2_distance']
        pool_saving = c0['container_pool']-c1['container_pool']
        saving = baseline['cost']-best['cost']
        assert math.isclose(saving, distance_saving+pool_saving, abs_tol=1e-6)
        item = dict(family=case['family'], best_joint_model=best['model'],
            m3_cost=baseline['cost'], best_joint_cost=best['cost'], saving=saving,
            saving_percent=saving/baseline['cost']*100, weighted_distance_saving=distance_saving,
            pool_saving=pool_saving)
        findings['per_case'].append(item)
        lines.append(f'| {case["family"]} | M{best["model"]} | {item["saving_percent"]:.2f}% | {fmt(distance_saving)} | {fmt(pool_saving)} | {fmt(saving)} |')
    search_changes = dict(improved=sum(item['reduction_percent'] > 1e-7 for item in comparisons),
        worsened=sum(item['reduction_percent'] < -1e-7 for item in comparisons),
        unchanged=sum(abs(item['reduction_percent']) <= 1e-7 for item in comparisons))
    findings['same_price_search_changes'] = search_changes
    lines += ['', '## 同价旧方案与新搜索', '',
        '两列均按本轮清洗0.2的新参数计价；左列是上一轮箱池5重新搜索的保存方案按箱池15重新计价，右列是本轮重新搜索，不把参数调整本身当作优化收益。旧方案的审计文件哈希和新价格重算均已核对。', '',
        f'同价比较中，新搜索{search_changes["improved"]}次更低、{search_changes["worsened"]}次更高、{search_changes["unchanged"]}次相同。有限时间、单种子的新搜索没有保证优于已保存旧方案；两轮结果均完整保留。', '',
        '| 算例 | 模型 | 旧方案按新价 | 新搜索 | 降低比例 | 旧池→新池 |',
        '|---|---|---:|---:|---:|---|']
    for item in comparisons:
        lines.append(f'| {item["family"]} | M{item["model"]} | {fmt(item["old_route_new_prices"])} | {fmt(item["fresh_search_cost"])} | {item["reduction_percent"]:.2f}% | {item["old_pool"]}→{item["new_pool"]} |')
    lines += ['', '## 清洗0.8的同方案计价对照', '',
        '主实验使用0.2；另将同一已审计方案按0.8计价，没有另做0.8搜索。在完整回收时，每个方案增加0.6Q，运输/箱池的理论权衡和同例方案排名不变。有限预算启发式搜索轨迹仍可能受总目标常量和机器负载影响，不能把这个计价对照称为独立的0.8优化。', '',
        '| 算例 | 模型 | 清洗0.2总成本 | 清洗0.8同方案总成本 | 增量 |',
        '|---|---|---:|---:|---:|']
    alt = json.loads((out/'alternate_cleaning_comparison.json').read_text(encoding='utf-8'))
    for item in alt['rows']:
        lines.append(f'| {item["family"]} | M{item["model"]} | {fmt(item["primary_clean02_total"])} | {fmt(item["alternate_clean08_total"])} | {fmt(item["total_difference"])} |')
    lines += ['', '## 边界与复现', '',
        '- 本轮本机Python3.9.6；上一轮Windows Python3.11.3。按墙钟截止的搜索受运行环境和负载影响，不能将路线变化全部归因于箱价。',
        '- 原速度2坐标单位/分钟；把距离解释为公里时相当于120公里/小时。本轮没有修改速度，不证明深圳实际运营可行。',
        '- 单种子、有限预算可行解，不是全局最优或稳定性证明。四进程共享机器，300秒为软截止。',
        '- 模型3将300秒用于单批次距离搜索；模型6–8将总预算分给三个K，各K预算、迭代和候选池在count_candidates.json中。若联合模型更差，不能据此推断其理论最优值更差。',
        '- 模型3/5仍优化两级原始距离和；模型6–8优化本轮加权费用。除日历机制外，目标和搜索过程也有差异。',
        '- 三张底图的50/100点为嵌套规模，不是六个独立总体样本；保留全部失败，不把无解费用记为零。',
        '- 原时间表最低需箱参考只重算模型3/5箱池费，不改变路线，也不是箱池优化输出；见comparison_cost.json。', '',
        '复核：`python output/model35678_comparison/20261006_truck_tricycle_pool15_5min_v1/verify_and_report.py`。复核支持仓库路径变化，检查冻结字节、重新审计30个方案、重算新目标、优化箱池、清洗箱次和双周期可复用性。', '',
        '重新运行应把run_study.py、monitor_study.py、verify_and_report.py、report_content.py复制到output/model35678_comparison/下一个新的空目录，然后运行run_study.py与verify_and_report.py；已有注册目录拒绝覆盖。', '',
        '主要文件：experiment.json、runs.csv、results/、verification.json、pool_cycle_cleaning_diagnostics.json、count_candidates.json、findings.json、same_price_search_comparison.csv、alternate_cleaning_comparison.json，以及冻结inputs/、cases/、source_snapshot/。']
    lines += ['', '## 最终方案费用分项与路程', '',
        '| 算例 | 模型 | D1 | D2 | 卡车费用 | 三轮费用 | 箱池费用 | 清洗 | 追踪 | 磨损 | 总费用 |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in sorted(rows, key=lambda item:item['id']):
        if not row['valid']:
            continue
        comp = row['cost_components']
        audit = json.loads((out/'results'/row['id']/'independent_audit.json').read_text())
        values = [audit['distances']['e1'], audit['distances']['e2'],
                  comp['e1_distance'], comp['e2_distance'], comp['container_pool'],
                  comp['cleaning'], comp['tracking'], comp['container_wear'], row['cost']]
        lines.append(f'| {row["family"]} | M{row["model"]} | ' + ' | '.join(fmt(v) for v in values) + ' |')
    write(out/'findings.json', findings)
    write(out/'reduced_candidate_tradeoffs.json', tradeoffs)
    (out/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
