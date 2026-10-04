"""Render the complete recorded comparisons after independent verification."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent


def table(pairs):
    lines = ['| 模型/种子 | 原距离 | 候选距离 | 原下界 | 候选下界 | 原 Gap | 候选 Gap |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for p in pairs:
        lines.append(f'| M{p["model"]}/{p["seed"]} | {p["objective_before"]:.4f} | {p["objective_after"]:.4f} | '
            f'{p["bound_before"]:.4f} | {p["bound_after"]:.4f} | {100*p["gap_before"]:.3f}% | {100*p["gap_after"]:.3f}% |')
    return '\n'.join(lines)


def main():
    summary = json.loads((OUT/'summary.json').read_text())
    validation = summary['validation']
    pairs = summary['comparisons']
    rows = summary['records']
    cycle = [p for p in pairs if p['batch']=='full20']
    causal = [p for p in pairs if p['batch']=='causal20']
    follow = [p for p in pairs if p['batch']=='causal30']
    timing = ['| 候选/种子 | 原函数秒 | 候选函数秒 | 原 Work | 候选 Work |',
              '| --- | ---: | ---: | ---: | ---: |']
    for p in pairs:
        if p['model']==1:
            timing.append(f'| {p["method"]}/{p["seed"]} | {p["wall_before"]:.4f} | {p["wall_after"]:.4f} | {p["work_before"]:.4f} | {p["work_after"]:.4f} |')
    changed = ', '.join(validation['changed_files']) or '无'
    document = f'''# 单批次 M1–M4：完整整数模型的二环约束与次序边界

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Mode: run; continuous user-authorized implementation, sequential frozen comparisons
- Verification Status: EXECUTED; {validation['tests_passed']} full tests, {summary['audits_passed']} independent schedule audits and periodic replays
- Scope: B=1, M1–M4 complete integer-time MILP; two separate optional candidates

## 本轮结论

实现并验证两项完整整数模型强化：任务弧的二环禁用行，以及任务/路线次序变量的隐含上下界和逐边 Big-M。
两项都保持原任务集合、距离目标与可行整数排程，没有新增连续时钟或删去回收任务。
20 客户四模型、两种子各自对照后，均没有稳定的整体收益。
M4 次序边界在 20 客户的两个种子均提高了下界，但可行解和 Gap 仍有退步；30 客户补测结果见下文。
因此 `_single_batch_cycle_cuts=False`、`_single_batch_causal_bounds=False` 继续作为默认，保留显式实验入口。
没有把更小的 Big-M、某个种子的较好下界或完成池内最优当作整体精确求解加速。

## 二环约束

原模型对每条被选择的 E2 任务边 `a -> b` 强制 `step[b] >= step[a]+1`。
所以两个相反方向的任务边不能同时出现；将它直接写成 `x[a,b]+x[b,a] <= 1` 是有效不等式。
零行驶、零服务时长也有严格次序增长；同一客户的不同订单、配送与回收分别保留任务 ID。
已有弧聚合用于共享路线索引与压缩路线表达，不增设变量，不增设回调，也不关闭原容量割回调。
只为实际存在的互逆弧对添加一行，20 客户 M1/M2/M3/M4 分别增加 8/25/27/82 行。

这项约束能切去部分满足宽 Big-M 次序行的分数二环；例如两条相反边各取 0.75 时，新行会拒绝。
测试还遍历 6 个任务的全部排列及所有相邻路线划分，共 23,040 个基本路线图，直接检查生成行的系数。
这是有效性与局部松弛强化证据，不是全部实例根节点下界必定变好的证明。

20 客户，每次完整求解总预算 20 秒：

{table(cycle)}

四模型与两个种子结果全部保留；M2 两组距离与 Gap 退步，M3/M4 方向混合。
M1 两组仍完成相同全局最优证明，速度方向也不一致。

## 次序上下界与 Big-M

令 `T` 为全部必访 E2 任务数，沿用已有的次序范围 `L=max(1,3*T-1)`。
次序变量是消除有向环与表达跨趟因果关系的辅助变量，不是物理时钟。
每个被访问任务有前驱和后继，因此 `1 <= step[task] <= L-1`。
可复用模型还要求每个订单配送在回收之前：`step[p] >= 2`，`step[d] <= L-2`。
配送和回收允许出现在不同趟次，物理使用时间下限、回仓、供货与库存约束仍由原模型保证。

对某车辆从零开始的第 `k` 个趟次，前缀使用顺序保证其前面有 `k` 个非空趟次。
每个前驱至少经过“趟次开始 -> 一个任务 -> 趟次结束 -> 下一趟开始”，因而可以使用：

```text
3*k <= step_start[k] <= L-2
3*k+2 <= step_end[k] <= L
```

未使用趟次的辅助次序也可落在这些区间，但其物理开始与结束时刻依然为零。
所有未激活的线性次序连接按新的端点范围重算松弛量，保证它们不限制任何区间内取值：

```text
step[right] >= step[left]+1-M*(1-arc)
M = max(0, UB(left)+1-LB(right))
```

指示约束保留原逻辑，仅利用更紧的变量界；任务到任务、路线边界、车辆前后趟次三个位置均处理。
原整数可行排程的因果图有 `T+2*R` 个有效节点，且非空趟数 `R<=T`；
按最长前驱路径重建辅助次序，可在原 `3*T-1` 范围内同时满足这些下界和上界。
对未使用路线，重新计算的松弛量使连接自动失效，因而正的辅助下界不会强迫其实际出车。

没有增加变量、普通行或指示约束。20 客户 M2 最大显式次序 M 从 60 降到 58，M3/M4 从 120 降到 118。
M1 当前默认表达使用指示约束，显式次序 M 统计为 0；不能将它解释为 M1 没有次序关系。
这项改进幅度有限，不能据此声称此前模型的大 M 已经构成主要瓶颈。

20 客户，每次总预算 20 秒，使用单独冻结的新批次基线：

{table(causal)}

M1 两候选的实际完成时间与确定性 Work：

{chr(10).join(timing)}

M2 次序边界两组距离退步，虽然其下界略升；M3 同一可行距离下的下界均退步。
M4 两组下界提高、距离一好一差，仍不足以将其设为默认。
两个候选批次的基线搜索进度不同，不能跨批次拼接最有利的数值作为候选收益。

## 30 客户 M4 补测

补测在 20 客户全部结果完成后选择，原因仅是 M4 下界在两个种子均提高；
20 客户 M2/M3 与 M4 的退步没有排除或覆盖。
同样是 20 秒总预算，种子 42/43、相同冻结初解，种子 43 反转方法顺序。
这属于开发过程中的有条件补测，没有独立留出集的泛化结论。

{table(follow)}

30 客户 seed 42 下界小幅提高，seed 43 下界降低；Gap 同样一好一差。
此补测没有确认 20 客户的稳定下界收益，不采用为默认。

## 验证与执行边界

两项候选分别具有 64 组完整整数最优值/不可行状态对照：四模型、四小实例种子、两种路线表达与两种额外表达选项。
参考值来自独立枚举原问题，覆盖有向非度量距离、重复位置和零耗时。
还固定实际 Gurobi 初始解的路线选择与整数时刻，验证跨趟回收、闲置趟次、非规范车辆编号及多趟起点。
零耗时每任务一趟的极限链到达最后任务 `L-1` 和最后路线结束 `L`，检验上下界不会少留一个位置。
检查默认矩阵尺寸、原容量割候选激活范围、错误参数、构建期限，以及每条回收任务完整覆盖。

最初二环针对性测试 277 项通过。次序测试第一次有 4 项在求解前被审计拒绝，
原因是测试更换最大趟数后没有同步初解政策元数据，日志保留；同步元数据后 457 项通过。
初次实验脚本还有一次多余括号导致的启动语法错误，发生在创建实验批次和执行求解之前；
`run_compare_initial.py` 与 `setup_failure.json` 保留该错误，不是求解运行的重试。
此后各冻结批次均不覆盖、不重试失败求解，也没有挑选后续重复结果代替第一次输出。

完整回归：**{validation['tests_passed']} passed，{validation['warnings']} warnings，{validation['pytest_runtime']:.2f} 秒**。
运行前后本轮 6 个运行时/测试文件均未变化，运行时哈希匹配最终冻结版本。
全量运行期间记录的共享 Python 文件变化：{changed}；不将其他聊天的后续编辑宣称为已验证。
所有 {summary['integer_runs']} 份返回整数排程均通过独立审计与周期回放，配送与回收任务集合完全一致。
实际全局最优证明共 {summary['original_global_optimal_proofs']} 份，均由本轮完整 MILP 完成；不继承冻结初始解的最优标记。
这些均为同一 20 客户 M1 实例的不同方法/种子运行，M2–M4 的本轮定时运行均未完成全局最优证明。

各次定时运行都使用单线程、独立串行进程；方法使用同份审计通过的冻结初解与原始输入。
预算包括预检查、原初解准备、建模、整数搜索与内部审计，未使用根节点列生成或路线池。
原求解器的小额初解构造仍按原流程运行；没有将其时间排除，或宣称它已被关闭。
这些直接完整 MILP 结果与此前列生成/初解池混合入口的数值不能作同预算成对比较。
各方法可得到不同分支、节点和可行解，数学等价或更紧的松弛不保证限时求解速度提高。

## 文件与复现

- `models/routing/solvers/exact/single_batch_short_cycles.py`：二环有效行。
- `models/routing/solvers/exact/single_batch_formulation.py`：次序界、端点松弛量与显式选项。
- `models/routing/solvers/exact/formulation.py`、`solver.py`：仅启用时传参，完整模型负责全局证明。
- `tests/routing/test_single_batch_short_cycles.py`、`test_single_batch_causal_bounds.py`：原问题独立对照、实际行与固定初解验证。
- `output/experiments/single_batch_short_cycles_20261001/`：方案、全部冻结数据/源码/初解/求解日志、原始失败、回归快照与最终清单。

实验目录中的 `full20/` 使用已归档且哈希验证的 `run_compare_cycles.py`；
`causal20/`、`causal30/` 使用当前 `run_compare.py`。
三个批次各自冻结源码，二环批次与后续次序批次的源码差异完整写入 `summary.json`。
重现时使用新输出目录；禁止覆盖已有结果。

```bash
.venv/bin/python output/experiments/single_batch_short_cycles_20261001/run_compare.py --customers 20 --models 1 2 3 4 --budget 20 --methods baseline causal --output reproduce_causal20
.venv/bin/python output/experiments/single_batch_short_cycles_20261001/run_compare.py --customers 30 --models 4 --budget 20 --methods baseline causal --output reproduce_causal30
```
'''
    target = ROOT/'docs/single_batch_causal_bounds_20261001.md'
    target.write_text(document)
    print(target)


if __name__=='__main__':
    main()
