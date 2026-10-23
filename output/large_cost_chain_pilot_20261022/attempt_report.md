## Material Passport

- Origin Skill：academic-research-suite / experiment-agent
- Origin Mode：run，大规模小样本对照
- Origin Date：2026-10-22（Asia/Shanghai）
- Verification Status：COMPLETED_AND_AUDITED（四次执行及独立双周期审计通过）；重复性与性能泛化未验证
- Version Label：m5_large_cost_chain_pilot_v1

# 第四轮尝试：100 客户大规模算例

**新版在两个 100 客户算例上都降低了总成本：L06 下降 9.10%，L07 下降 1.55%。** 两策略每例各跑 60 秒、seed=42，共四次完整求解；四份最终方案均通过独立双周期审计。本轮没有修改算法，也没有根据输赢调参、增加种子或重复求解。

L06 的收益主要来自箱池达到下界后仍继续合并一级运输；L07 则再次体现多用箱子换取更少运输的成本取舍。不过，L07 新修复已用满约 15% 的预算，却只产生一次直接接受的改进，仍需验证和控制开销。默认开关继续关闭。

## 1. 案例选择与控制条件

当前正式数据集最大规模为 100 客户；本轮只选其中两个 M5 大例，没有自行生成更大规模输入，也没有运行全套案例。案例选择依据输入结构，先于本轮求解结果确定。

| 案例 | 客户 / 箱次 | 卫星数 | 批次数 | 各批需求 | 一级车辆 / 每卫星二级车辆 |
| --- | ---: | ---: | ---: | --- | ---: |
| L06 | 100 / 100 | 5 | 3 | 20、50、30 | 5 / 6 |
| L07 | 100 / 100 | 3 | 4 | 15、35、35、15 | 5 / 10 |

L06 延续前期已经试过的大例；L07 具有连续两个需求为 35 的批次，用于观察多时点库存瓶颈。所有输入保持原样，成本继续采用合成单位：`0.1 D1 + 0.05 D2 + 5 N + 0.02 Q`。

两策略使用**同一份冻结源码与输入**，仅切换 `stock_chain_strategy`：

- 旧版：`critical_stock_repair=True, stock_chain_strategy='stock_first'`。
- 新版：`critical_stock_repair=True, stock_chain_strategy='cost_aware'`。
- 各 60 秒、seed=42、迭代上限 10⁹，无外部热启动；E2 日程缓存关闭。
- 串行、独立子进程，数值库线程上限 1，`PYTHONHASHSEED=0`。顺序为 L06 旧→新、L07 新→旧，减少始终由同一策略先运行的顺序偏差，但不能消除机器负载差异。
- 修复参数未改：宽度 2、最多 4 层、每节点最多 32 候选、每次最多 192 次评价；每 150 次迭代最多触发一次，单次约 0.4 秒，累计约占总预算最多 15%。
- 完整调用耗时约 60.022–60.034 秒。外部独立审计额外约 0.003 秒，不计入求解时间；求解器内部审计仍在预算内。单次原子评价无法中途截断，故修复预算可有毫秒级超出。

[预先保存的协议](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/large_cost_chain_pilot_20261022/protocol.json) · [大例输入清单](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/large_cost_chain_pilot_20261022/case_inventory.json) · [运行参数与环境](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/large_cost_chain_pilot_20261022/experiment/manifest.json)

## 2. 同预算最终结果

| 案例 | 旧版成本 | 新版成本 | 成本下降 | 旧版 N → 新版 N | 独立双周期审计 |
| --- | ---: | ---: | ---: | ---: | --- |
| L06 | 402.432607 | **365.799170** | **9.1030%** | 50 → 50 | 两版通过 |
| L07 | 484.259811 | **476.753227** | **1.5501%** | 39 → 45 | 两版通过 |

### L06：箱池不变，一级运输显著减少

| 指标 | 旧版 | 新版 |
| --- | ---: | ---: |
| 一级距离 D1 | 954.900488 | 591.256667 |
| 二级距离 D2 | 1098.851167 | 1093.470056 |
| 一级 / 二级趟数 | 7 / 36 | 4 / 40 |
| 装载时点条件箱池需求 | [20,50,50] | [20,50,50] |

总成本减少 **36.633438**：一级运输费用减少 36.364382，二级运输费用减少 0.269056，其余费用相同。二级趟数虽然增加，但当前配置的每趟固定费用为零，不能只看趟数判断经济效果。

两版 N=50 都达到必要下界 50，因此这个固定实例的最小箱池数量已达到；这不证明包含运输的总成本最优。旧策略因 N 已达下界，全程没有真正调用库存链搜索。新版仍尝试运输合并，产生 4 次经审计接受的改善。

第一次调用从 N=50、516.954894 出发，连续合并三个批次中 S5 的送货与回收车辆，成本依次为 502.089140、487.223385、472.357630，N 始终为 50。后续合并及原有 ALNS 继续改善至最终结果。这个例子主要提供了**到达箱池下界后继续做跨送货/回收运输合并的价值**，不能单独证明内部非支配筛选优于其他筛选规则。

### L07：更多箱子仍能换来较低总成本

| 指标 | 旧版 | 新版 |
| --- | ---: | ---: |
| 一级距离 D1 | 1674.718292 | 1449.060982 |
| 二级距离 D2 | 2395.759627 | 2096.942579 |
| 一级 / 二级趟数 | 12 / 55 | 10 / 48 |
| 装载时点条件箱池需求 | [15,39,39,39] | [15,45,45,45] |

新版多用 6 个箱子，增加箱池费用 **30**；一级运输减少 **22.565731**，二级运输减少 **14.940852**，合计运输节省 **37.506583**，所以总成本净降 **7.506583**。

L07 的现有预检给出的必要箱池下界为 **37**，高于单批最大需求 35：在第二批装载前，累计需要发出 50 箱，而乐观估计最多只能清洗释放 13 箱，因此至少需要 37 箱。该下界不等于已找到 37 箱可行方案，也不是总成本下界；本轮不能宣称箱池或总成本已最优。

新版第一次链修复从 N=43、541.495578 出发，先到 N=41、533.280381，再经历一个较贵中间状态，最后以 **N=45、532.930494** 被接受。之后没有更多直接接受的链修复，最终成本继续下降由原有搜索完成。初始联动改变了后续轨迹，但本轮不能把全部最终差值归因于这一操作。

[完整逐次结果](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/large_cost_chain_pilot_20261022/experiment/results.json) · [成本分解](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/large_cost_chain_pilot_20261022/analysis.json) · [简明 CSV](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/large_cost_chain_pilot_20261022/comparison.csv)

## 3. 运行开销与收敛

| 案例 / 策略 | CPU 秒 | 迭代数 | 评价缓存未命中 | 修复调用 / 候选评价 / 可行候选 | 修复秒 / 预算占比 | 直接接受的链改善 |
| --- | ---: | ---: | ---: | --- | --- | ---: |
| L06 旧版 | 54.319 | 17,062 | 16,310 | 0 / 0 / 0 | 0 / 0% | 0 |
| L06 新版 | 57.072 | 17,731 | 17,839 | 29 / 888 / 234 | 2.358 / 3.93% | 4 |
| L07 旧版 | 57.123 | 14,826 | 15,820 | 40 / 1,344 / 601 | 3.687 / 6.14% | 2 |
| L07 新版 | 55.246 | 13,804 | 15,785 | 33 / 2,549 / 742 | 9.002 / 15.00% | 1 |

修复候选评价可能命中已有缓存，不能与缓存未命中次数直接相加。两版墙钟预算相同，但实际 CPU 时间不同；不能仅凭迭代数认定新版更快或更慢，也不能把 L07 迭代减少全部归因于修复开销。

L07 新版累计修复耗时 9.002 秒，已触及 9 秒分配，末次原子评价造成约 2 毫秒超出；它在 2,549 个修复候选中只有 742 个可行，最终只有一次链改善被接受。这是本轮最明确的效率限制。L06 的修复开销则明显较小，且早期合并直接带来多次改善。

![两个大规模案例的收敛曲线](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/large_cost_chain_pilot_20261022/convergence.png)

曲线来自各自完整 60 秒运行的最优解历史。L06 新版早期便明显领先；L07 前十余秒旧版较好，之后新版反超。因此“60 秒终点较好”不代表任意更短预算下都更好，图中的时间截面也不等同于独立使用短预算重新运行的结果。

## 4. 与已有结果的关系

前期 L06 在 30 秒、seed=42 下，不启用库存链修复的结果为 409.870442；试用 E2 日程缓存的结果为 409.760941。本轮新版 365.799170 低于这两个历史值，但**预算从 30 秒变成 60 秒，不能把全部差值归因于新算法**。本轮正式效果以同为 60 秒的旧策略 402.432607 为比较对象。

历史材料的路径、哈希与原摘要保存于 [historical_context.json](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/large_cost_chain_pilot_20261022/historical_context.json)。本轮没有另跑一个全程关闭修复的控制组；L06 旧策略实际修复调用为零，但这一事实不替代跨案例的专门控制实验。

## 5. 判断与下一步

本轮说明这套成本兼顾的修复**在当前 100 客户规模上可以运行，并在两个已选案例中改善了 60 秒终点成本**。效果差异明显：L06 的运输合并机会较多，L07 的修复更费时，最终优势较小。两个例子都支持按完整经济成本评价箱池与运输的交换，不能把最小化 N 作为总代价的替代目标。

这些仍是每例一次、一个种子的探索结果，没有重复性、统计显著性或平均性能保证。没有精确总成本参照，不报告最优性 gap；也没有对超过 100 客户、M6–M8 或不同费用结构作验证。本轮不据此开启默认策略。

下一步优先在同样两个案例上预先固定少量新种子验证重复性。若继续改算法，L07 提示应尝试在连续无改善时降低调用频率或缩减预算，并用消融区分“送货/回收联动合并”与“成本兼顾的内部筛选”各自的作用。这些是后续建议，本轮尚未执行。

## 6. 可复现性与检查

沿用分支 `codex/event-evaluation-pilot`。新增大例驱动 `experiments/runs/pilot_m5_large_cost_chain.py`，只允许 1–2 个不同的大例；每个进程有 PID、退出码、30 秒存活检查、120 秒硬超时和完整日志。四次进程均正常退出，无超时、无重试，保存全部结果及审计。

本轮算法及工具源码与第三轮实测快照逐文件一致，未改评价器、审计器或成本合同。因此没有重复运行第三轮的整套算法回归；本轮完成了新增驱动的内存语法编译、三个 CLI 输入限制检查，以及四个实际实验的独立双周期审计。第三轮的 34 项专项通过、91 项基础通过是历史回归结果，不计作本轮新跑的测试。

冻结 ZIP、当前源码、输入和驱动副本经过哈希核对；前面三轮的归档也检查未被修改。`git diff --check` 通过。见 [驱动检查](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/large_cost_chain_pilot_20261022/driver_validation.json)、[最终校验](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/large_cost_chain_pilot_20261022/verification.json)和[进程记录](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/large_cost_chain_pilot_20261022/experiment/runs.json)。

- [L06 新版完整方案](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/large_cost_chain_pilot_20261022/experiment/quality/L06/cost_aware/result.json)及[独立审计](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/large_cost_chain_pilot_20261022/experiment/quality/L06/cost_aware/audit.json)。
- [L07 新版完整方案](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/large_cost_chain_pilot_20261022/experiment/quality/L07/cost_aware/result.json)及[独立审计](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/large_cost_chain_pilot_20261022/experiment/quality/L07/cost_aware/audit.json)。
- [冻结源码与两例输入](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/large_cost_chain_pilot_20261022/experiment/sources.zip)、[新增驱动补丁](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/large_cost_chain_pilot_20261022/experiment_driver.patch)。
- 两版各自的完整结果、摘要、审计与日志位于 `experiment/quality/<case>/<strategy>/`；绘图脚本为 `summarize.py`，另保存 SVG 图。

在项目根目录以新输出目录复现：

```sh
.venv/bin/python -m zipfile -e output/large_cost_chain_pilot_20261022/experiment/sources.zip /private/tmp/m5_large_cost_restore
.venv/bin/python experiments/runs/pilot_m5_large_cost_chain.py \
  --source /private/tmp/m5_large_cost_restore/sources/shared \
  --cases L06 L07 --seconds 60 --seed 42 \
  --output output/large_cost_chain_pilot_recheck
```

限时搜索受机器负载和墙钟进度影响，复跑不保证相同成本、计数或搜索轨迹；冻结材料保证可重建本轮输入与代码版本，不代表结果已获得跨运行复现验证。
