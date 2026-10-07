"""Report automatic batch-count selection, preserving the original experiment design."""
from collections import Counter
import json, math
MODELS=(3,5,6,7,8)
def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def render_report(out,plan,rows,summary,comparisons,diagnostics,candidates):
    fmt=lambda x:'—' if x is None else f'{x:,.2f}'
    joint=[r for r in diagnostics if r['model'] in (6,7,8)]
    single=[r['id'] for r in joint if r['actual_delivery_batches']==1]
    multi=[r['id'] for r in joint if r['actual_delivery_batches']>1]
    reduced=[r['id'] for r in joint if r['configured_or_optimized_pool']<r['total_box_uses']]
    empty=[r['id'] for r in joint if r['cycle_batch_count']>r['actual_delivery_batches']]
    tradeoffs=[]
    for identifier in sorted({c['id'] for c in candidates}):
        group=[c for c in candidates if c['id']==identifier and c.get('valid')]
        base=next((c for c in group if c['batch_count']==1),None)
        if not base: continue
        for c in group:
            if c['batch_count']==1: continue
            saving=25*(base['container_pool_size']-c['container_pool_size'])
            net=c['objective']-base['objective']
            transport=net+saving
            tradeoffs.append(dict(id=identifier,family=c['family'],model=c['model'],candidate_k=c['batch_count'],
                selected_k=c['selected_cycle_k'],baseline_pool=base['container_pool_size'],candidate_pool=c['container_pool_size'],
                baseline_cost=base['objective'],candidate_cost=c['objective'],pool_cost_saving=saving,
                weighted_transport_increase=transport,total_cost_increase=net,
                audit_scope='solver internal candidate summaries; final returned routes independently replayed'))
    winners=Counter()
    for case in summary['cases']:
        feasible=[r for r in case['selected'].values() if r]
        if feasible: winners[str(min(feasible,key=lambda r:r['cost'])['model'])]+=1
    findings=dict(selected_joint_solutions=len(joint),single_actual_delivery=single,multiple_actual_deliveries=multi,
        selected_pool_below_total_demand=reduced,empty_calendar_segments=empty,
        counts_by_model={str(m):dict(Counter(str(r['actual_delivery_batches']) for r in joint if r['model']==m)) for m in (6,7,8)},
        lowest_cost_counts_by_model=dict(winners),same_price_search_changes=dict(
            improved=sum(c['reduction_percent']>1e-7 for c in comparisons),
            worsened=sum(c['reduction_percent']<-1e-7 for c in comparisons),
            unchanged=sum(abs(c['reduction_percent'])<=1e-7 for c in comparisons)),reduced_candidate_tradeoffs=tradeoffs)
    lines=['# 箱价25、一级里程1：六例自动选择批次的正式实验','',
        '## Material Passport','', '- Origin Skill: academic-research-suite / experiment-agent',
        '- Origin Mode: run (continuous)', '- Origin Date: 2026-10-06',
        '- Verification Status: '+('VERIFIED' if summary['valid']==summary['planned'] else 'PARTIAL'),
        '- Version Label: pool25_truck1_autoK_5min_v1','',
        f'完成{summary["completed"]}/{summary["planned"]}次，独立路线与双周期复核通过{summary["valid"]}次。基准提交`{plan["commit"]}`。',
        '', '## 恢复原实验流程','',
        '六个原算例（50/100客户）、模型3/5/6/7/8、seed42、每模型每例总预算300秒、迭代上限1000000、4个worker。没有提前指定M6–M8最终批次。',
        '', 'M6–M8一次调用传入候选K=[1,2,3]，程序将总时间和迭代预算分配给各K，联合优化路线、时间表、订单批次和箱池，再自动返回有效候选中费用最低者。K的允许范围沿用原实验，不是无上限搜索。M3/M5保留原距离目标，M3固定单批、M5固定输入日历，作为原有基准。',
        '', '这不同于此前固定K诊断：此前每个K独享300秒并分开运行，最后横向比较；本轮每模型每例只有一个300秒总预算，最终K由求解器返回。固定K诊断结果不混入本轮正式实验结果。',
        '', '```mermaid', 'flowchart LR',
        'A[输入数据、成本和允许K范围] --> B[一次总预算300秒]',
        'B --> C[程序优化各K的路线、时间表和箱池]',
        'C --> D[检查候选可行性]',
        'D --> E[自动返回最低总成本的K及方案]',
        'E --> F[独立路线与双周期复核]', '```',
        '', '## 参数','',
        '`C = D1 + 0.056470588D2 + 25N + 0.6U + 0.2R`。磨损25/50=0.5，追踪0.1，清洗按每次实际回库0.2；完整回收U=R=Q，常量费用为0.8Q。工时、车辆固定和趟次费为0。',
        '', '主价格与最近固定K诊断完全一致：箱25、一级1、二级0.056470588。与之前六例自动K箱价15实验相比，仅改箱池15→25、磨损0.3→0.5、一级2.535→1；全部物理输入、时间窗、车队、清洗时长和308份算法源码保持。原距离与速度仍是基准单位，费用为synthetic_units。',
        '', '## 最终返回方案的成本','',
        '| 算例 | 点数 | Q | M3 | M5 | M6 | M7 | M8 | 最低费用模型 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---|']
    for case in summary['cases']:
        selected=case['selected']; feasible=[r for r in selected.values() if r]
        best=min(feasible,key=lambda r:r['cost']) if feasible else None
        values=[fmt(selected[str(m)]['cost'] if selected[str(m)] else None) for m in MODELS]
        lines.append(f'| {case["family"]} | {case["customers"]} | {case["demand"]} | '+' | '.join(values)+f' | M{best["model"] if best else "—"} |')
    lines+=['','![最终成本组成](cost_components.png)','',
        '## 自动选择的K、实际配送批次和箱池','',
        f'M6–M8最终18个方案中，实际单批{len(single)}个、多批{len(multi)}个、箱池小于Q的{len(reduced)}个；有空日历段{len(empty)}个。日历K不能替代实际配送批次。',
        '', '| 算例 | 模型 | 日历K | 实际配送批次 | N | Q | 每批发箱 | 每批回库 |',
        '|---|---|---:|---:|---:|---:|---|---|']
    for r in diagnostics:
        issue=', '.join(f'{k}:{v}' for k,v in r['demand_by_batch'].items())
        returned=', '.join(f'{k}:{v}' for k,v in r['returned_by_batch'].items())
        lines.append(f'| {r["family"]} | M{r["model"]} | {r["cycle_batch_count"]} | {r["actual_delivery_batches"]} | {r["configured_or_optimized_pool"]} | {r["total_box_uses"]} | {issue} | {returned} |')
    lines+=['', '## 程序内部各K候选','',
        '这里记录一次300秒调用内的各K候选、分配上限和实际运行秒数。提前结束的时间可留给后续K，所以分配上限之和不等于实际消耗；以整次调用总预算300秒为准。候选valid来自求解器内部检查；独立路线和双周期审计只针对最终返回的30条路线。复核确认最终成本等于有效候选成本的最小值。',
        '', '| 算例 | 模型 | 候选K | 分配上限秒 | 运行秒 | 候选N | 候选成本 | 状态 | 最终K |',
        '|---|---|---:|---:|---:|---:|---:|---|---:|']
    for c in candidates:
        lines.append(f'| {c["family"]} | M{c["model"]} | {c["batch_count"]} | {fmt(c["budget"])} | {fmt(c.get("runtime"))} | {c.get("container_pool_size","—")} | {fmt(c.get("objective"))} | {c["status"]} | {c["selected_cycle_k"]} |')
    lines+=['', '## 同次调用的省箱和运输权衡','',
        '比较本次运行的K1与其他有效K候选，正的费用增量表示更贵。共同完整发出/回收时箱次费用相同，成本差=运输增量−箱池费节省。',
        '', '| 算例 | 模型 | 候选K | 单批池→候选池 | 箱池节省 | 运输增量 | 总费用增量 | 最终K |',
        '|---|---|---:|---|---:|---:|---:|---:|']
    for t in tradeoffs:
        lines.append(f'| {t["family"]} | M{t["model"]} | {t["candidate_k"]} | {t["baseline_pool"]}→{t["candidate_pool"]} | {fmt(t["pool_cost_saving"])} | {fmt(t["weighted_transport_increase"])} | {fmt(t["total_cost_increase"])} | {t["selected_k"]} |')
    lines+=['', '## 前一轮正式实验的路线按新价重计','',
        '比较对象是六例自动K箱价15正式实验的最终保存路线，按本轮一级1/箱25计价；不是固定K诊断结果。重新计价和重新搜索分别列出，有限搜索不保证新结果更好。',
        '', '| 算例 | 模型 | 旧路线按新价 | 本轮新搜索 | 降低比例 | 旧N→新N |',
        '|---|---|---:|---:|---:|---|']
    for c in comparisons:
        lines.append(f'| {c["family"]} | M{c["model"]} | {fmt(c["old_route_new_prices"])} | {fmt(c["fresh_search_cost"])} | {c["reduction_percent"]:.2f}% | {c["old_pool"]}→{c["new_pool"]} |')
    lines+=['', '## 最终方案的费用分项','',
        '| 算例 | 模型 | D1 | D2 | 一级费 | 二级费 | 箱池费 | 清洗 | 追踪 | 磨损 | 总费用 |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in sorted(rows,key=lambda r:r['id']):
        if not row['valid']: continue
        a=json.loads((out/'results'/row['id']/'independent_audit.json').read_text());c=row['cost_components']
        values=[a['distances']['e1'],a['distances']['e2'],c['e1_distance'],c['e2_distance'],c['container_pool'],c['cleaning'],c['tracking'],c['container_wear'],row['cost']]
        lines.append(f'| {row["family"]} | M{row["model"]} | '+' | '.join(fmt(v) for v in values)+' |')
    lines+=['', '## 范围和复核','',
        '- 同价参数、同物理输入、同每次总预算；M3/M5与M6–M8的目标和预算分配结构仍不同，排名不能解释为理论最优值排序。',
        '- 无热启动；单个种子、有限预算，没有全局最优证明。50/100点为嵌套规模，不是六个独立总体样本。',
        '- K范围沿用1/2/3；不是让K无限增大，也没有更改批次搜索算法。',
        '- 清洗0.8只对同一已审计路线另外计价，保存在alternate_cleaning_comparison.json，未另做搜索。',
        '- 原速度2坐标单位/分钟、周期和所有约束保持；不证明深圳实测运营可行性。',
        '- 审计核对冻结哈希、费用公式、终端回库洗净、连续双周期、全部候选范围和程序自动选最低费用。',
        '', '复核：`python output/model35678_comparison/20261006_pool25_truck1_autoK_5min_v1/verify_and_report.py`。',
        '', 'results/保存最终路线、独立审计和费用；count_candidates.json保留程序候选；verification.json保留自动选择及双周期检查。']
    write(out/'findings.json',findings);write(out/'candidate_tradeoffs.json',tradeoffs)
    (out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
