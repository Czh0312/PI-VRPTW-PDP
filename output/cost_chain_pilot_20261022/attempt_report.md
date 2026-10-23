## Material Passport

- Origin Skill：academic-research-suite / experiment-agent
- Origin Mode：run，单案例对照与共同起点机制诊断
- Origin Date：2026-10-22（Asia/Shanghai）
- Verification Status：VERIFIED（运行记录、完整成本及双周期可行性）；性能泛化待验证
- Version Label：m5_cost_chain_pilot_v1

# 第三轮尝试：兼顾成本的库存链修复

**本轮完整搜索取得改善，但共同起点的单次局部修复没有成功。** 只跑 M02 一个正式案例，旧库存优先修复版与新版各 30 秒、seed=42。新版总成本 **138.140023、箱池 N=7**，比旧修复版的 **151.136896、N=5** 低 **8.5994%**，也比此前已知方案 **138.690672** 低 **0.3970%**。所有正式方案通过独立双周期审计。

这支持继续研究兼顾运输与箱池的联动搜索，尚不足以证明稳定优势。本轮保持试验开关默认关闭，未扩展 M6–M8，也未追加其他案例或根据结果挑选种子重跑。

## 1. 本轮改变了什么

沿用已建立的分支 `codex/event-evaluation-pilot`。修改前先冻结工作区已有实现，保留前两轮全部成果。新增 `models/routing/model_5/alns/cost_chain.py`，通过以下显式参数使用：

```python
solve_alns(problem, max_time=30, max_iterations=10**9, seed=42,
           critical_stock_repair=True, stock_chain_strategy='cost_aware')
```

默认 `critical_stock_repair=False`；启用修复但不指定策略时，仍使用旧版 `stock_first`。

上一轮内部优先消除库存缺口，容易集中在“小箱池、高运输费”的区域。本轮把每个可行节点表示为**各装载时点的条件箱池需求向量，以及完整费用中扣除箱池后的费用**。后者包括全部非箱池费用，并非只计算距离。

- 在这些标签间做非支配筛选，按总成本和库存进展交替保留分支，宽度仍为 2。相同标签只保留一个节点；标签不含全部路线与未来调度信息，因此这是启发式截断，不是严格支配证明。
- 交替生成原有库存修复与新增一级路线合并候选。合并包含把卫星移入另一车辆已有配送路线，以及联动回收车辆提示、保留提示、清空提示三种选择；按配送距离节省排序，但最终仍计算完整成本。
- 箱池达到必要下界时也允许继续尝试运输改进；中间步骤可以增加 N 或暂时增加总成本，只在非箱池费用降低、某个装载需求下降或完整成本下降时继续保留。
- 只把相对于本次起点**完整总成本更低**的方案交回主搜索。全部候选仍经过原有评价器；新最优方案和最终方案仍独立审计。

开发测试发现：只合并送货路线，原回收车辆提示可能保留另一辆车出车；简单清空提示也不保证合并。因此加入送货与回收车辆的联动候选。两卫星构造例子已验证，达到最小箱池后仍可减少一级趟数并降低成本。

物理评价器、独立审计器、成本参数及数据未改。本轮冻结源码间仅有 `cost_chain.py`、`search.py`、`solver.py` 三个模型文件不同。见 [校验记录](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/cost_chain_pilot_20261022/verification.json)。

## 2. 实验范围与预算

M02：20 客户、20 箱次、3 卫星、4 个固定批次，每批需求 5。沿用合成单位成本：

`总成本 = 0.1 D1 + 0.05 D2 + 5 N + 0.02 Q`。

正式对照共 **2 次完整求解**，两版均开启库存链修复，分别使用旧策略和新策略。各 30 秒、seed=42、迭代上限 10⁹，无外部热启动；串行、独立子进程，顺序旧版→新版，`PYTHONHASHSEED=0`，数值库线程上限 1。E2 日程缓存均关闭。

两策略都限定每次最多 192 次候选评价、每节点最多 32 个候选、最多 4 层、宽度 2；主循环每 150 次迭代最多调用一次，单次时间分配最多约 0.4 秒，累计约不超过总预算 15%。单次原子评价无法中途截断。完整调用实际耗时约 30.014 秒，外部审计在计时外，内部审计仍计入预算。

另做 **2 次共同起点诊断**：两策略分别从同一份历史成本 138.690672 的已审计方案出发，仅调用一次修复，最多 2 秒、192 次评价。它们不混入正式求解，也不充当热启动。

实验开始前保存了 [协议](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/cost_chain_pilot_20261022/protocol.json)。本轮没有新跑“不启用修复”的完整基线；因此正式比较回答的是“新版相对旧修复版如何”，而不是新版相对原始 ALNS 的稳定平均优势。

## 3. 同预算结果及成本解释

| 指标 | 旧库存优先修复 | 新成本兼顾修复 |
| --- | ---: | ---: |
| 总成本 | 151.136896 | **138.140023** |
| 箱池 N | 5 | 7 |
| 一级距离 D1 | 1068.584745 | 847.057835 |
| 二级距离 D2 | 377.568437 | 360.684784 |
| 一级 / 二级趟数 | 8 / 14 | 6 / 14 |
| 迭代数 | 28,291 | 27,286 |
| 完整候选评价缓存未命中次数 | 26,168 | 26,077 |
| 进程 CPU 秒 | 29.094 | 27.868 |
| 完整调用墙钟秒 | 30.014 | 30.015 |
| 独立双周期审计 | 通过 | 通过 |

新版少花 **12.996874**，相对下降 **8.5994%**。其中多用 2 个箱子增加费用 **10**；一级运输费用减少 **22.152691**，二级运输费用减少 **0.844183**，其他项相同。选择较大的箱池在当前费用下更便宜。

此前已知方案为 N=7、138.690672。本轮新版低 **0.550650（0.3970%）**：箱池费用相同，一级运输费用增加 0.002872，二级运输费用减少 0.553522。该历史方案用于衡量是否超过已有结果，不替代本轮同步对照。本轮仍没有总成本最优性证明。

![M02 两策略收敛曲线](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/cost_chain_pilot_20261022/convergence.png)

曲线直接来自保存的最优解历史。新版在第 2,648 次迭代、约 4.806 秒时达到最终成本；之后继续运行到约 30 秒。右图按前 3,000 次迭代显示轨迹，不能据此推断跨运行的收敛率或平均性能。

原始结果见 [results.json](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/cost_chain_pilot_20261022/experiment/results.json)，成本差值见 [analysis.json](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/cost_chain_pilot_20261022/analysis.json)。

## 4. 搜索中观察到的机制

新版修复调用 **7 次**，评价 **1,344** 个候选，其中 **820** 个通过完整评价，累计 **1.313 秒**，约占预算 **4.38%**，带来 **3 次**经审计接受的新最优方案。旧版调用 4 次、评价 559 个候选、耗时 0.587 秒，带来 1 次新最优方案。评价计数包括从评价缓存返回的候选，不能与缓存未命中数直接相加。

第 1 次迭代的起点已经是最小箱池 N=5，成本 187.767536。新版仍继续做运输合并，经过暂时更贵的中间状态，最终返回 **N=8、187.355143**，成本降低约 0.412393。旧策略在 N 达到下界时直接跳过修复。这个实例直接说明，允许增加箱数可以帮助降低完整成本。

第 151 次迭代，起点 N=8、167.813894；修复完成以下链条：

| 步骤 | 操作 | N | 完整成本 |
| --- | --- | ---: | ---: |
| 1 | S5 / b1 重建二级送取并联动一级车辆 | 7 | 170.464577 |
| 2 | b1 合并 S4 送货与回收车辆 | 7 | 162.973656 |
| 3 | b4 合并 S4 送货与回收车辆 | 7 | 155.482734 |
| 4 | b2 合并 S4 送货与回收车辆 | 7 | 148.111201 |

第一步降低箱池却增加成本，后续运输合并才使整条链值得接受。第 1,951 次迭代另有一次链修复将成本从 139.734696 降至 138.814553，之后原有搜索继续到最终 138.140023。

这些日志证明联动操作在此次运行中发挥了作用；最终改善是新增候选、内部筛选、到达下界后仍搜索，以及原有 ALNS 后续探索共同形成的结果。本轮没有分别关闭这些部件做消融，不能把全部改善归因于非支配筛选或某一个算子。

## 5. 共同起点诊断：两版都没有改进

两版重建历史参考方案后，成本均精确恢复为 138.690672，N=7，装载需求向量为 [5,7,7,7]。

| 指标 | 旧策略 | 新策略 |
| --- | ---: | ---: |
| 最终保留成本 | 138.690672 | 138.690672 |
| 返回改善方案 | 否 | 否 |
| 候选评价 / 可行候选 | 160 / 106 | 192 / 117 |
| 到达深度 | 3 | 4 |
| 墙钟秒 | 0.145 | 0.188 |
| 找到的最小箱池 | 5 | 5 |
| 该最小箱池方案成本 | 151.375673 | 151.614449 |

两份 5 箱方案均另行通过双周期审计，但总成本高于起点，均被拒绝。新版在这个诊断中评价更多，也没有获得更好结果。因此本轮证据是“从初始构造出发的完整搜索结果改善”，不能写成“新版能够直接突破历史较好方案的局部最优”。

诊断记录：[旧策略](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/cost_chain_pilot_20261022/experiment/mechanism/baseline/summary.json)、[新策略](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/cost_chain_pilot_20261022/experiment/mechanism/candidate/summary.json)。对应目录保存全部候选评价记录、最终方案和最低箱池方案及各自审计。

## 6. 判断、限制与下一轮建议

本轮更支持将 **ALNS + 小规模成本兼顾的跨层联动修复** 作为当前 M5 的候选方向。箱池最小值 N=5 已知，但成本目标不应围绕 N=5 单向推进。建模与两阶段求解之间应传递各装载时点的库存需求和清洗释放信息；只传一个 N 或把运输安排固定后再单独压缩箱池，容易丢失有价值的交换。

当前证据只有一个案例、一个种子；墙钟调度会改变搜索轨迹，两次 CPU 时间也不同。没有统计显著性、跨案例稳定性或精确最优 gap 的依据。低费用标签不能代表未来可行性；宽度 2、192 次评价可能遗漏有利分支。新的联动合并只是有限候选，并未完整优化一级路线。

下一轮宜先**验证重复性，而非立即加复杂度**：仍只用 M02，预先固定两个新种子，各比较旧策略和新策略；如果改善可重复，再选第二个案例，或做“仅加合并、不改筛选”的消融。本轮尚未执行这些后续实验。

M6–M8 的批次分配与日历可变，不能直接沿用固定日历下的标签与下界结论。需要把日历变更后的 E2 实际回仓、截点归属、一级回收与清洗重新评价，再研究这类修复。本轮没有修改或声称验证它们。

## 7. 验证、材料与复现

- 专项测试 **34 passed**：包含跨多个库存瓶颈的成本障碍、保留较大箱池的低费用标签、箱池到下界后继续合并、送取覆盖与输入不被修改、评价预算/截止时间/重复起点、显式策略参数和独立周期重放。
- 基础检查 **91 passed、15 skipped、14 warnings**。跳过项为本机 Gurobi 许可证于 2026-10-21 到期；未计作通过。本轮 ALNS 正常运行，告警为已有依赖弃用提示。
- 构造例子开发阶段出现过合并不降费的测试失败，用于暴露送货与回收未联动的问题；在正式实验前完成修复。正式 4 个工作进程均成功退出，全部审计通过，未因结果输赢追加重跑。
- 模型工作文件与实测候选源码哈希一致；数据一致；前两轮归档校验未变；`git diff --check` 通过。

[测试日志与命令](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/cost_chain_pilot_20261022/tests/summary.json) · [实验清单](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/cost_chain_pilot_20261022/experiment/manifest.json) · [简明 CSV](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/cost_chain_pilot_20261022/comparison.csv) · [本轮实现补丁](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/cost_chain_pilot_20261022/round3_implementation.patch) · [两版冻结源码与 M02 输入](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/cost_chain_pilot_20261022/experiment/sources.zip)

[本轮最好方案](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/cost_chain_pilot_20261022/experiment/quality/candidate/result.json)及其[独立审计](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/cost_chain_pilot_20261022/experiment/quality/candidate/audit.json)已保存。实验驱动原样保存在 `experiment/driver.py`，图表由 `summarize.py` 从原始结果生成，另有 SVG 版本。

在项目根目录使用新输出目录复现：

```sh
.venv/bin/python -m zipfile -e output/cost_chain_pilot_20261022/experiment/sources.zip /private/tmp/m5_cost_chain_restore
.venv/bin/python experiments/runs/pilot_m5_cost_chain.py \
  --baseline-source /private/tmp/m5_cost_chain_restore/sources/baseline \
  --source /private/tmp/m5_cost_chain_restore/sources/candidate \
  --reference output/cost_chain_pilot_20261022/experiment/inputs/anchor_result.json \
  --seconds 30 --seed 42 \
  --output output/cost_chain_pilot_recheck
```

限时完整搜索不保证在另一机器负载下复现相同最终成本或计数。只做共同起点诊断时，可使用同一驱动的 `--worker mechanism --variant baseline` 或 `--variant candidate`，分别指向对应冻结源码、同一参考方案和新的输出目录。
