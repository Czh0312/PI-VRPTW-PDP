"""Record all unsuccessful trials without claiming an adopted improvement."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
shuffle = json.loads((OUT/'comparison.json').read_text())
restart = json.loads((OUT/'restart_comparison.json').read_text())
ps = json.loads((OUT/'shuffle_pilot_comparison.json').read_text())
pr = json.loads((OUT/'restart_pilot_comparison.json').read_text())
fixed = json.loads((OUT/'context_fixed_work_summary.json').read_text())
v = json.loads((OUT/'verification.json').read_text())
assert not any(r['adopt'] for r in shuffle['decisions']+restart['decisions']) and not fixed['adopted']
rows = []
for m in range(1, 5):
    a = next(r for r in shuffle['summary'] if r['model']==m and r['budget']==5)
    b = next(r for r in shuffle['summary'] if r['model']==m and r['budget']==15)
    rows.append(f"| M{m} | {a['mean_change_percent']:+.2f}% | {a['improved']} / {a['equal']} / {a['worse']} | {a['maximum_regression_percent']:+.2f}% | {b['mean_change_percent']:+.2f}% | 撤回 |")
pilot_rows = [f"| M{m} | {ps['summary'][m-1]['mean']:+.2f}% | {pr['summary'][m-1]['mean']:+.2f}% |" for m in range(1,5)]
cache_rows = [f"| {r['family']} | {r['before_cpu']:.6f} | {r['after_cpu']:.6f} | {r['cpu_reduction_percent']:.2f}% | 是 |" for r in fixed['summary']]
a, b = restart['summary']
report = f'''# 单批次 ALNS：修复与重启优化试验（2026-10-01）

## Material Passport

- Origin Skill：academic-research-suite / experiment-agent。
- Origin Mode：validate，用户授权的单批次 M1–M4 连续优化。
- Verification Status：VERIFIED，限于保存的源码、配对、独立审计、回归和工作区复现。

## 本轮结论

**三类候选均未达到事先登记的采用条件，已全部撤回；求解器保留上一版。** 本轮涉及的 `single_batch.py`、`neighborhoods.py`、`partitioned_assignment.py` 与轮前快照逐字节一致。共享工作区其他改动没有被整体恢复。

完成72对同时间质量实验，基线与候选各72次全部通过独立审计，候选全部通过周期重放；另完成12次固定迭代效率实验。不能把开发均值的改善写成最终版本收益。

## 试验一：随机修复顺序

保留截止时间顺序修复，另加入随机移除完整订单、随机排列插入顺序的算子；所有配送完成插入后再插入回收。每步仍用原时间/容量评分选择最低分位置，并由完整解码和独立审计决定可行性。

开发16对平均距离下降，但正式验证32对未满足门槛。距离变化先按每对 `(候选/基线-1)×100%` 计算，再等权平均；负数表示距离下降。每模型六对5秒、两对15秒。

| 模型 | 5秒平均距离变化 | 改善 / 持平 / 退步 | 5秒最大单次退步 | 15秒平均距离变化 | 决定 |
|---|---:|---:|---:|---:|---|
{chr(10).join(rows)}

## 试验二：从最好解破坏修复后重启

把每150迭代的随机初解重启改为破坏修复已审计的最好解，并使用该解对应的分仓与评分上下文。开发16对中仅M2达到筛选条件，其余模型未进入正式验证。

M2正式验证：5秒平均变化 **{a['mean_change_percent']:+.2f}%**，仅 **{a['improved']}/{a['pairs']}** 对改善，五对退步，最大单次退步 **{a['maximum_regression_percent']:+.2f}%**；15秒平均变化 **{b['mean_change_percent']:+.2f}%**，两对都改善。六对短预算至少三对改善的条件未满足，故撤回。

两项开发试验的完整均值均保留：

| 模型 | 随机修复开发平均变化 | 最好解重启开发平均变化 |
|---|---:|---:|
{chr(10).join(pilot_rows)}

## 试验三：M2跨子求解复用评分上下文

为完整顾客分仓建立最多两个求解内上下文，保留最好分仓及一个近期备选。复用准备表、路线和一级解码结果，不改变随机数、算子、评分公式、重启与下降策略；新求解不复用旧上下文，其他分仓不共享解码结果。

两组100顾客，固定300迭代，seed42，每版三个独立进程重复；基线/候选交替串行，60秒上限不触发。CPU不含数据加载、外部审计及profiler。12次运行的最终路线、分仓、目标值和迭代数逐值一致且审计有效。

| 来源 | 基线CPU中位数 / 秒 | 候选CPU中位数 / 秒 | CPU耗时下降 | 解一致 |
|---|---:|---:|---:|---|
{chr(10).join(cache_rows)}

采用规则在测量前登记：两个来源CPU中位数均至少下降2%，所有固定迭代输出一致且有效。来源5-1b未达到门槛，故撤回。计划中的八对同时间质量作业按这个门槛取消，清单保留；没有把未执行作业计入质量分母。

## 协议、回归与边界

- 两项质量策略在看到开发数据前分别登记门槛和正式验证计划。开发按模型筛选：平均变化非正，至少半数改善，审计有效。正式采用要求短、长预算均值非正，至少半数短预算改善，任何单次退步不超过5%，全部独立审计/周期重放有效。没有观察后改阈值。
- 随机修复开发：来源5-2场景02，50/100顾客，seed42/43，5秒，16对；验证：来源5-3场景02，嵌套50/100，seed45/46/47，5秒，24对，另来源10-1场景02，100顾客，seed45/46，15秒，8对。
- 最好解重启开发：同开发母例，seed48/49，16对；M2验证：来源10-2场景02，嵌套50/100，seed50/51/52，5秒，六对；来源5-3场景02，100顾客，seed50/51，15秒，两对。
- 源码与输入冻结，前后次序交替，串行独立进程运行。本任务未在质量实验同时执行CPU基准或回归测试。所有失败、退步、候选与取消作业保留。
- 候选修复16项新增测试、重启7项测试、上下文2项测试分别通过对应定向检查；随被否决实现归档。初次完整检查曾遇到共享工作区另一项M5改动的两项失败，其定向复查和完整重跑通过，原日志保留；本轮没有修改该项M5实现。
- 最终回归：**{v['final_regression']}**。最终源码与工作区在两个100顾客来源、四模型、固定300迭代的八组结果逐值一致，全部重新独立审计和周期重放有效。
- 最后一次源码和输入SHA256核验无漂移。母几何在此前轮次出现过，不是完全未见数据；嵌套规模和场景存在依赖。少量种子只提供描述统计，机器负载未完全控制。不宣称显著性、全数据集改善、15秒以上表现或已证最优。

统计谬误检查11/11：模型/预算分层；不外推局部均值；说明来源选择和依赖；不作碰撞变量调整；保留完整分母；不挑极端种子；不删退步；不按显著性挑结果；预登记且保存全部选择；说明负载限制；固定预算避免所讨论的反向因果问题。

## 复核入口

- [随机修复全部结果](../output/experiments/alns_single_batch_repair_20261001/comparison.json)
- [最好解重启全部结果](../output/experiments/alns_single_batch_repair_20261001/restart_comparison.json)
- [上下文复用固定迭代结果](../output/experiments/alns_single_batch_repair_20261001/context_fixed_work_summary.json)
- [质量采用门槛](../output/experiments/alns_single_batch_repair_20261001/pilot_manifest.json)
- [重启验证计划](../output/experiments/alns_single_batch_repair_20261001/restart_validation_plan.json)
- [效率采用门槛](../output/experiments/alns_single_batch_repair_20261001/context_protocol.json)
- [最终验证记录](../output/experiments/alns_single_batch_repair_20261001/verification.json)
- [工作区复现](../output/experiments/alns_single_batch_repair_20261001/live_equivalence.json)
- [最终回归日志](../output/experiments/alns_single_batch_repair_20261001/full_regression.log)

复算：同目录 `run_pairs.py`、`summarize.py`、`context_fixed_work.py`、`check_final.py adopted_source`。当前工作区保留轮前算法，试验必须使用对应冻结源码。
'''
(ROOT/'docs/alns_single_batch_repair_20261001.md').write_text(report)
print('report written')
