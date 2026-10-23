## Material Passport

- Origin Skill：academic-research-suite / experiment-agent
- Origin Mode：run，按用户授权连续完成一个改动的配对试验
- Origin Date：2026-10-23（Asia/Shanghai）
- Verification Status：COMPLETED_AND_AUDITED；两个性能运行及独立双周期审计通过，泛化效果未验证
- Version Label：m5_marginal_pickup_pilot_v1

# 第五轮尝试：一级回收边际成本候选

**这轮没有改善 L07：当前成本兼顾修复为 476.257330，加入回收换车候选后为 481.154556，终点成本高 1.0283%。** 两版 N 均为 45，一级运输距离相同，差额全部来自二级运输。本轮只用一个算例、seed=42，每版各运行一次 60 秒，没有追加种子、重试或按结果改参数。

新算子没有产生进入修复候选流的方案，也没有贡献被接受的改进。对两份最终方案的静态检查说明：一级回收已全部附着在配送路线的末站，单独把回收点换车没有运输节省空间。这个具体邻域暂不值得成为默认策略；保留为显式可选试验。

## 改动和实验控制

沿用分支 `codex/event-evaluation-pilot`。新增 [marginal_pickup.py](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/model_5/alns/marginal_pickup.py)，通过 `critical_stock_repair=True, stock_chain_strategy='cost_aware_marginal'` 启用。基线仍是当前 `cost_aware`，不是更早的 `stock_first`。

算子从完整解码得到的一级路线出发，将某个卫星的整份回收任务移到另一辆车。枚举目标回收序列的插入位置，按来源与目标路线的距离、占用工时、趟次和车辆启用费用变化排序，产生局部可行且预测节省运输费用的候选。只改变回收车辆提示，配送路线、二级路线和日历不变。原解码器仍可重新选择插入位置，因此排序值是候选估计，不能充当全目标的安全下界。

新候选与旧修复候选交替进入同一搜索，仍受每父节点 32 个候选、宽度 2、深度 4、192 次评价、每次约 0.4 秒、总修复预算约 15% 的限制。仍每 150 次迭代触发；未加入降频、Regret、MILP 简化或其他新算子。完整评价重新计算清洗释放、后续车辆复用和 N，只有完整成本改善才返回，新最优方案和最终方案仍独立审计。默认策略未改变。

| 控制项 | 本轮设置 |
| --- | --- |
| 模型 / 案例 | M5 / L07，100 客户、3 卫星、4 批次 |
| 批次需求 | 15、35、35、15 |
| 随机种子 / 每策略次数 | 42 / 1 |
| 时间预算 | 每策略 60 秒墙钟，迭代上限 10⁹ |
| 顺序 | 当前 cost_aware → cost_aware_marginal，独立子进程串行 |
| 初解 / 缓存 | 无外部热启动；E2 日程缓存关闭 |
| 费用 | 0.1 D1 + 0.05 D2 + 5 N + 0.02 Q，输入保持不变 |
| 监控 | 30 秒进程检查，120 秒硬超时；两次均正常退出，无重试 |

两版使用同一份冻结源码和输入，仅策略参数不同。已有成本兼顾修复和解码器源码未改；既有算法文件仅 `search.py` 增加分派与新算子计数。冻结材料核对了 360 个文件。见[预先保存的试验协议](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/marginal_pickup_pilot_20261023/protocol.json)、[运行清单](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/marginal_pickup_pilot_20261023/experiment/manifest.json)和[入口改动补丁](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/marginal_pickup_pilot_20261023/search_change.patch)。

## 同预算结果

| 指标 | 当前 cost_aware | 加入回收边际成本候选 |
| --- | ---: | ---: |
| 总成本 | **476.257330** | 481.154556 |
| 箱池 N | 45 | 45 |
| 一级距离 D1 | 1430.036901 | 1430.036901 |
| 二级距离 D2 | **2125.072797** | 2223.017309 |
| 一级 / 二级趟数 | 10 / 49 | 10 / 50 |
| CPU 秒 | 56.911 | 56.655 |
| 完整调用墙钟秒 | 60.067 | 60.026 |
| 迭代数 | 14,275 | 14,371 |
| 评价缓存未命中 | 16,627 | 16,425 |
| 独立双周期审计 | 通过 | 通过 |

新版总成本多 **4.897226**，对应二级距离多 **97.944512**；一级费用只有浮点求和级别差异，箱池和其他费用相同。两版装载时点条件箱池需求都是 `[15,45,45,45]`。必要箱池下界为 37，但不意味着已找到 37 箱可行解，更不是总成本最优证书。

| 修复统计 | 当前 cost_aware | 新版 |
| --- | ---: | ---: |
| 修复调用 | 36 | 33 |
| 候选评价 / 可行 | 2,951 / 806 | 2,647 / 749 |
| 修复秒数 | 9.015910 | 9.004065 |
| 预算占比 | 15.03% | 15.01% |
| 直接接受的链改善 | 1 | 1 |
| 新算子进入候选流 / 完整评价 / 可行 | 不适用 | **0 / 0 / 0** |

单次原子评价不能中途截断，所以软时间预算有毫秒级超出。两版唯一接受的修复链相同：原有库存修复与两次配送/回收合并，将初始成本 541.495578 降到 532.930494，链中没有新算子。之后的改进来自原有搜索。

![L07 单次配对收敛曲线](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/marginal_pickup_pilot_20261023/convergence.png)

两条曲线早期很接近，中后期存在交叉。结论是“本次 60 秒终点新版较差”，不能扩大成所有预算下都较差。新邻域虽然没有提供被评价的候选，枚举仍需时间；墙钟驱动的冷却、修复截断和机器负载都可能改变后续搜索轨迹。没有配对重复或额外消融，不能把 1.03% 全部解释成某个确定的开销效应。

[原始结果](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/marginal_pickup_pilot_20261023/experiment/results.json) · [成本分解与接受链](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/marginal_pickup_pilot_20261023/analysis.json) · [简明 CSV](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/marginal_pickup_pilot_20261023/comparison.csv)

## 为什么没有有效的新候选

本轮额外检查的是已经保存的最终路线，没有重新求解、增加案例或增加种子。两份方案的结果一致：

- 10 条一级路线均只有一个回收站，且就是该路线最后一个配送站。移除回收任务后，这辆车仍必须完成配送并返仓，无法节省原本的往返距离。
- 共检查 56 个单点换车插入位置，52 个有局部违规，4 个局部可行但不节省运输费。
- 违规计数有重叠：49 次一级返仓超批次结束、15 次终端车辆时间违规、18 次终端未完成清洗；这三项不能直接相加当作不同候选数。
- 所有 56 个插入位置的预测运输费用差都为正，最小也增加约 1.902408。原候选生成函数在两份最终方案上均返回 0 个候选。

这解释了最终状态中该邻域缺少机会。运行计数还表明，整个限时搜索中新算子进入候选流的数量为 0；没有保存所有中间状态的逐位置诊断，不能把最终状态的 52/4 分类外推到每次调用。

上一轮距离 22→20 的局部例子和本轮单元测试证明了这种邻域在特定结构中有价值，但它们不能保证 L07 恰好存在同样结构。本轮现有算子已把回收安排在配送末站，进一步收益更可能需要同时改变配送路线或二级回收时间。当前 `cost_aware` 已有配送/回收联动候选，应优先改善其候选质量和开销，而非重复增加单点移动。

见[已保存方案诊断](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/marginal_pickup_pilot_20261023/saved_schedule_diagnosis.json)和[诊断脚本](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/marginal_pickup_pilot_20261023/diagnose_saved.py)。

## 检查、结论与后续

新增及相关修复测试共 **24 项通过**，另有成本、缓存、强化和多批次基础回归 **66 项通过、16 项跳过**。跳过来自已于 2026-10-21 过期的 Gurobi 许可证，包括共用许可证 fixture 的测试；不能算作通过。14 条警告为 Matplotlib/Pyparsing 的既有弃用提示。该实验不使用 Gurobi。

新增测试覆盖：有向非对称距离、含工时/趟次/车辆费用的增量核对、保持配送和二级任务不变、状态不被修改、评价与时间上限，以及“运输省钱但错过清洗复用边界、N 从 2 增至 4、全成本上升时拒绝候选”。两个实际最终解均通过独立双周期审计。驱动的单案例、零预算和非有限预算输入检查通过；`git diff --check` 通过。前四轮归档共 205 个校验项保持一致。

本轮保留试验开关，继续使用当前 `cost_aware` 作为下一轮基线。用户的一种子要求继续适用。下一项更值得试的是修复连续无改善时降频，或者对库存链重建加入安全可行性预筛；两者仍应分开试验。本轮没有实施这些改动，没有修改 MILP 或 M6–M8，也不报告统计显著性或最优性 gap。

[验证记录](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/marginal_pickup_pilot_20261023/verification.json) · [回归日志](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/marginal_pickup_pilot_20261023/regression.log) · [冻结源码与输入](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/marginal_pickup_pilot_20261023/experiment/sources.zip)

复现时在项目根目录使用新的输出目录：

```sh
.venv/bin/python -m zipfile -e output/marginal_pickup_pilot_20261023/experiment/sources.zip /private/tmp/m5_marginal_restore
.venv/bin/python experiments/runs/pilot_m5_marginal_pickup.py \
  --source /private/tmp/m5_marginal_restore/sources/shared \
  --cases L07 --seconds 60 --seed 42 \
  --output output/marginal_pickup_recheck
```

每次重新运行都受墙钟和负载影响。冻结材料提供可重建的输入与源码，不保证相同种子复跑产生逐位相同的搜索轨迹。10 月 22 日的历史 `cost_aware` 成本为 476.753227；本轮重新运行基线为 476.257330，正式比较采用本轮同预算配对结果，不从历史运行中择优。
