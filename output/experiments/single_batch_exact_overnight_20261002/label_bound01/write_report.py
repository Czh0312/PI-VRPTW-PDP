"""Build an interim report from both separately verified frozen versions."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def table(summary):
    lines = ['| 客户/种子 | 原距离 | 候选距离 | 原 Gap | 候选 Gap | 原根秒 | 候选根秒 |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for p in summary['pairs']:
        lines.append(f'| {p["customers"]}/{p["seed"]} | {p["objective_before"]:.4f} | {p["objective_after"]:.4f} | '
            f'{100*p["gap_before"]:.3f}% | {100*p["gap_after"]:.3f}% | {p["root_before"]:.4f} | {p["root_after"]:.4f} |')
    return '\n'.join(lines)


def main():
    one = read(OUT/'full60/verification/summary.json')
    two = read(OUT/'full60_v2/verification/summary.json')
    regression = read(OUT/'regression.json')
    # Every recorded version has its own original labels, data, source and
    # common start; do not substitute a favorable cross-batch baseline.
    rows = one['records']+two['records']
    changes = ', '.join(regression['changed_files']) or '无'
    text = f'''# 单批次标签定价：返仓下界与批量发现的夜间试验

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Mode: run; continuous user-authorized overnight implementation and frozen comparisons
- Verification Status: {one['audits']+two['audits']} independent schedule audits/replays, {one['pricing_checks']+two['pricing_checks']} large pricing checks, {regression['passed']} full tests
- Scope: B=1 M1/M2 optional LP delivery pricing; large performance comparisons are M2 only

## 当前阶段

实现两版可证明安全的标签定价剪枝，保留首版的小规模耗时退步和全部对照。
首版 30 客户两个种子均在约 16 秒完成根 LP，原方法耗满约 30 秒仍未完整结束定价，
最终可行距离从 661.6478 降至 660.2517/660.0032；20 客户根阶段略慢、距离一致。
第二版保留批量负列发现，根阶段更快，但 20 客户最终下界下降、30 客户 seed43 最终上界显著退步；
其端到端稳定收益门槛未通过，不能仅凭根阶段速度采用。完整结果见下表。
当前仅一个来源块、20/30 两种规模和两个种子，外部夜间负载无法完全控制，
不能据此声称 M1–M4 全面加速或进行默认采用。`label_bound_pruning=False` 仍是默认。

## 有效性

原标签算法对每个 `(visited_mask,last)` 保留时间/费用的 Pareto 前沿，
利用剩余重量与体积的分数背包奖励上界，为每个尚未展开状态生成完整路线费用下界。
此前找到可行最好路线后仍会继续穷举大量无法改善最小值的状态。

新选项从任务到返仓点求非负弧费用的最短路，允许已访问任务、不可行中间点与重复任务，
放松时间和容量，所以它是实际初等路线后缀费用的下界。
弧费用包含原始有向距离与非负时间对偶罚项；阶段一没有距离费用，逻辑仍有效。
不能用原始直返弧代替这个下界：非度量矩阵允许绕行更便宜。
对最短路结果减去缩放的浮点余量，加入原标签堆键。
最短路计算中断时，暂定距离是上界，全部丢弃并退回零下界；已测试部分节点处理后的中断。

首版当最小未处理堆键不小于已重放的最好完整路线费用时证明定价完成。
被 Pareto 支配但仍留在堆中的过期状态只可能降低最小堆键，不会错误终止。
检查在上一状态的全部扩展处理后进行，标签/时限/发现列上限中断仍沿用保守前沿下界。

首版的提前最小值证明也会减少同轮可发现的负列，使下一轮 LP 次数增加。
20 客户迭代 21→35；30 客户 42→69，尽管后者仍显著缩短根阶段。
第二版在批量发现模式只于前沿达到 `max(best,0)` 后关闭；
若最好费用为负，此时证明不存在未处理负列，同时证明最小值。
普通完整最小值调用仍按 `best` 关闭；原负列批量上限不变。
停止更保守，不删除合法状态或资源规则，允许保留更多可改善当前主问题的路线。

独立容量只容纳一个任务、价格不同的反例验证：即使已经证明最好单路线，
其余负费用单路线仍全部返回，且与独立排列枚举一致。
新测试的第一次版本错误要求保留所有等价访问排列，包含原 Pareto 支配本就删除的路径；
改为上述真正检验批量发现的反例后通过，失败日志未覆盖。

## 固定历史对偶诊断

从旧冻结完整 MIP 定价证书重构最后两轮对偶，核对原问题指纹与客户唯一订单映射。
20/30 客户、种子 42/43、每轮三仓，共 24 组；无剪枝与剪枝都完整结束，
最小约化费用均与独立的历史完整 MIP 一致。
展开状态合计 388,884→32,024，减少约 91.77%；20/30 分别减少 85.04%/92.64%。
这是确定性的机制诊断，不是 24 组独立来源，也不是端到端加速证据。
诊断 CPU 时间受其他任务影响，只记录、不作干净耗时结论。

## 首版同总预算完整求解

总预算 60 秒，根上限 30 秒，整数可行路线池上限 5 秒；输入、初解与源码均冻结。
有效分仓对偶行、compact 货流池及完整联合 MILP 原逻辑相同，只切换标签候选。
单线程、串行独立进程，seed 43 反转方法顺序，不重试、不覆盖。

{table(one)}

## 第二版同总预算完整求解

独立新批次，相同注册预算与种子；首版源码保存在 `candidate_source_v1/`，两批各有完整运行快照。

{table(two)}

第二版 20 客户根阶段约 0.93 秒，原约 1.37/1.48 秒，但最终 Gap 从约 6.01%
上升至 7.146%/6.997%；同一预算中的距离不变。
30 客户根阶段 7.83/9.05 秒且均完整结束定价，原约 30 秒均未结束；
seed42 可行距离 710.9439→660.0032、Gap 17.159%→7.659%，
seed43 却为 661.6478→711.4359、Gap 7.888%→14.334%。
因此第二版确实改善定价机制，但整数路线池质量和后续完整 MILP 仍有波动，
本次整体性能结论为混合，默认采用拒绝。两批原方法在相同计时上限下的结果也不同，
外部负载与限时中断位置影响不得省略，更不能跨批选择较弱的原方法作为对照。
后续优先分离根对偶行组成、受限池初解以及整数搜索各自的影响，
只在提出新的可验证假设后进行下一批对照。

下界在相同原问题有效范围内，原根与候选根的数值余量会有微小差别。
根 LP 的 `LP_OPTIMAL` 与 `relaxation_complete` 只表示松弛完成，
整数路线池最优也只在受限集合内成立；本轮 {len(rows)} 次大规模定时运行的完整整数全局最优证明均为零。

## 验证

首版联合针对性验证 602 项通过，补充部分最短路中断反例后新文件 83 项通过；
第二版新批量反例修正后联合 604 项通过。
独立原问题小例枚举和路线排列覆盖有向非度量、重复任务位置、零时长、两个容量、
阶段一/二、非负时间对偶罚项及全部标签/期限/列中断方式。
M3/M4 没有接入配送定价原型，其原回收与真实车辆规则保持原模型处理。

本轮两版 {len(rows)} 份完整排程均通过独立审计与周期回放，界均不超过审计后的可行上界。
最终保留对偶平面中有实际定价记录的子问题另用独立整数时钟 MIP 核查，共 {one['pricing_checks']+two['pricing_checks']} 项，
其中 {one['complete_mip_price_checks']+two['complete_mip_price_checks']} 项由该 MIP 完整结束。
若 MIP 限时，验证脚本转用不剪枝的完整标签枚举，并要求确实完整结束。
第二版 30 客户 seed42 原方法因中断，最后一轮只有一个仓的实际定价记录，
其余两个仓沿用原证书的保守回退系数，不计作完成的定价核查。
该核查只覆盖最终保留平面的已有定价记录，不冒称复核了全部仓或所有历史主问题对偶。

完整回归：**{regression['passed']} passed，{regression['warnings']} warnings，{regression['pytest_seconds']:.2f} 秒**。
本轮四个运行时/测试文件前后未变，运行时哈希与最终冻结第二版匹配。
回归期间共享 Python 文件变化：{changes}；其他聊天的后续变化不作已验证声明。

每次性能运行保存其他夜间聊天前后检查点，无法完全控制的外部机器负载构成实验限制。
本批是开发对照；需要独立来源、额外种子和短/长预算复核，才讨论默认采用。
所有负结果、测试数据错误、首版和第二版都分别保留，没有用不同批次更有利的基线替换。

## 文件

- `models/routing/solvers/exact/single_batch_label_bound.py`：允许非度量绕行的返仓费用下界与中断降级。
- `single_batch_route_pricing.py`：前沿最小值证明、批量发现和原资源/中断逻辑。
- `single_batch_columns.py`：仅显式调用时启用的 `label_bound_pruning`；MIP 模式拒绝误用。
- `tests/routing/test_single_batch_label_bound.py`：独立枚举、期限与批量反例。
- `output/experiments/single_batch_exact_overnight_20261002/label_bound01/`：方案、全部冻结输入/源码/初解/日志/结果、定价复核和测试快照。

早期 `stage_manifest.json` 是进行中结果的阶段哈希，不是最终结果清单；最终独立清单另存。
次日总报告由今晚后续试验继续汇总，此文仅记录已完成的标签候选阶段。
'''
    path = ROOT/'docs/single_batch_label_bound_20261002.md';path.write_text(text)
    final_files = [p for directory in (OUT/'full60',OUT/'full60_v2') for p in directory.rglob('*')
                   if p.is_file() and 'source_snapshot' not in p.parts]
    final_files += [OUT/'plan.json',OUT/'plan_v2.json',OUT/'regression.json',OUT/'full_regression.log',path]
    (OUT/'final_result_manifest.json').write_text(json.dumps({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in final_files},ensure_ascii=False,indent=2)+'\n')
    print(path)


if __name__=='__main__':
    main()
