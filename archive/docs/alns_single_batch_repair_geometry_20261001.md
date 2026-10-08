# 单批次 ALNS：修复阶段复用距离缓存（2026-10-01）

## Material Passport

- Origin Skill：academic-research-suite / experiment-agent。
- Origin Mode：validate，用户授权的单批次 M1–M4 连续优化。
- Verification Status：VERIFIED，限于冻结源码、固定工作量、等时配对、独立审计、回归和工作区复现。

## 最终采用范围

**仅保留M3破坏修复阶段的距离缓存复用。** 两组100顾客、scenario01、seed78、固定300迭代的CPU中位耗时分别下降5.39%和2.85%，最终路线、分仓、目标值和迭代数全部一致。M1/M2/M4未启用本次缓存；之前的M3下降邻域优化继续保留。

M3重新确认的10对等时实验全部通过独立审计及周期重放。5秒平均距离变化+0.17%，最大单次退步7.16%；15秒两对均改善，平均距离变化-3.31%。两个更广范围的候选分别因M4长预算退步和综合提速不足而撤回，详细记录如下。此次主要是降低计算开销，不能宣称所有实例/预算的路线质量都改善，也未证明最优性GAP缩小。

## 候选实现

贪心修复在比较插入位置时，原先直接调用问题实例的距离API，重复查询已有评分器缓存中的弧。原全部启用候选在B=1、启用准备评分器且实际使用`SingleBatchRouteScorer`时，复用该评分器最多4096项的上下文内LRU距离缓存，用于原路线距离及插入距离下界。任意自定义评分函数和多批次路径继续使用原距离API。

`(a,b)`与`(b,a)`分别缓存；不假设对称性或三角不等式。完整路线距离仍按原顺序累加，插入筛选公式、容差、严格小于的平局处理及候选遍历顺序保持原样。只复用静态距离；货物准备、车辆就绪、配送/回收前序、容量、时间窗及库存仍重新评分、完整解码并独立审计。M3此前采用的下降邻域缓存保留。

14项新增测试覆盖有向/非度量/零距离、重复顾客的多个订单、非整数载荷、紧时间窗、同车多次行程、改变准备/等待时间、缓存复用及容量、自定义评分器回退、新问题几何变化和错误上下文拒绝。与未准备的原评分和距离路径逐值比较。定向回归89项通过。

## 预登记与固定工作量

先冻结基线、输入，登记采用规则，再测量候选。两个100顾客来源5-3/10-2、scenario02，四模型、seed70、固定300迭代、60秒上限，每版三个独立进程重复，交替串行，共48次。CPU计时包含求解内检查，排除加载、profiler和额外独立审计。上限未触发。

采用门槛：八组CPU中位耗时比的综合几何平均降幅至少2%；每模型两个来源的几何平均降幅非负；任一组中位耗时不得变慢超过5%；全部前后最终路线、分仓、目标值和迭代数逐值一致且审计/周期重放有效。任一项失败即拒绝整个候选，不在本协议内事后选择模型子集。

| 模型 | 来源5-3 CPU耗时减少 | 来源10-2 CPU耗时减少 | 两来源综合降幅 |
|---|---:|---:|---:|
| M1 | 2.88% | 2.50% | 2.69% |
| M2 | 0.92% | 1.44% | 1.18% |
| M3 | 3.23% | 2.64% | 2.94% |
| M4 | 4.48% | 2.53% | 3.51% |

综合几何平均降幅2.58%，八组结果均逐值一致，48次独立审计及周期重放全部通过，进入等时质量比较。

## 等时质量协议

短预算：来源5-2、scenario02的嵌套50/100顾客，四模型，seed71/72，5秒，共16对。长预算：来源10-1、scenario02的100顾客，四模型，seed71/72，15秒，共8对。每对交替版本先后顺序，保留全部24对、48次结果，包含失败与退步。

每对距离变化为`100 × (候选距离 / 基线距离 − 1)`，负值表示路线距离减少。按模型及预算分组取等权均值。每组平均退步不超过2%、任一单次退步不超过10%，所有审计/周期重放有效；任何失败则拒绝整个候选。

首轮24对、48次审计及周期重放全部有效。但M4两对15秒实验分别退步7.82%和7.12%，该模型/预算平均退步7.47%，超过2%门槛，**全部启用候选否决并撤回**。M4的5秒平均距离变化为-1.86%，不能用短预算改善抵消长预算失败。

| 模型 | 5秒平均距离变化 | 15秒平均距离变化 |
|---|---:|---:|
| M1 | -0.40% | +1.61% |
| M2 | +0.20% | +0.15% |
| M3 | -0.78% | +0.97% |
| M4 | -1.86% | +7.47% |

## 第二候选：M1–M3范围

在首轮结果之后，另登记M1–M3范围的确认协议。新增`cache_distances`参数默认关闭，仅固定分仓的单批次破坏修复调用显式开启，M4连续分仓搜索及所有分仓投影均关闭。该实现与原全部启用候选存在范围差异，不能直接套用原CPU结果。

两个100顾客来源5-2/10-1、scenario02，seed74，四模型、固定300迭代、每版三个进程重复，共48次。所有前后路线、分仓、目标值和迭代数一致，审计及周期重放全部有效。M1/M2/M3两个来源的综合CPU降幅分别1.54%/0.95%/2.77%，三模型整体1.76%，未达到预登记2%门槛，**候选否决并撤回**。M4为未启用缓存的控制，两来源综合CPU波动-0.41%。原计划18对质量作业取消，清单保留，未计入执行分母。

## 第三候选：M3单独确认

依据前两轮选择M3子集，另行登记确认协议。实际启用条件为`problem.reusable and _assignment_search is None`，只有公开求解入口中的M3破坏修复开启；M1/M2/M4均关闭。多批次路径和自定义评分器继续使用原距离API。18项新增测试包含四模型公开入口开关范围验证，最新联合定向回归93项通过。

换用scenario01，来源5-2/10-1的100顾客、seed78、固定300迭代、每版三个独立进程重复，交替串行，共12次。

| 来源 | 基线CPU中位数 / 秒 | M3候选CPU中位数 / 秒 | CPU耗时减少 |
|---|---:|---:|---:|
| 5-2 scenario01 | 0.928871 | 0.878811 | 5.39% |
| 10-1 scenario01 | 0.684938 | 0.665392 | 2.85% |

两来源综合几何平均CPU降幅4.13%。全部路线、分仓、目标值与迭代数逐值一致，全部审计/周期重放有效。新门槛要求两个来源各至少提速2%，已通过。场景和种子在此次测量前指定；母几何并非从未观察过，M3也经过子集选择，不将这12次当作未选择的普遍性能证据。

随后预登记的M3质量确认：上述两来源的嵌套50/100顾客、seed77/78、5秒，共8对；两个100顾客、seed79、15秒，共2对。每预算平均退步不超过2%、任一单次不超过10%，全部审计/周期重放有效，任一失败则拒绝M3候选。本轮不再选择进一步的子集。10对、20次求解全部审计及周期重放有效，按原门槛采用M3范围。

| 预算 | 配对数 | 平均距离变化 | 改善 / 持平 / 退步 | 最大单次退步 |
|---|---:|---:|---:|---:|
| 5秒 | 8 | +0.17% | 4 / 0 / 4 | +7.16% |
| 15秒 | 2 | -3.31% | 2 / 0 / 0 | 0 |

进一步按规模描述：5秒的50顾客四对平均-0.98%，100顾客四对平均+1.33%；15秒100顾客两对平均-3.31%。这些细分是描述性结果，不是事后更改采用门槛。5秒和15秒使用不同种子，不能仅从两个均值推断预算的因果效应。15秒只有两个配对，保留小样本限制。

## 最终验证

- 完整回归：1343 passed, 14 warnings in 50.28s；本轮新增18项测试，其他测试包含共享工作区已有及其他任务的改动。
- 全部固定工作量比较：三个候选合计108次求解，全部独立审计/周期重放有效，每组所有最终解逐值一致。
- 执行质量比较：原候选24对、M3确认10对，共34对、68次，全部独立审计/周期重放有效。第二候选原计划18对取消，没有计入已执行分母。
- 最后八组冻结源码/工作区结果逐值一致，全部审计及周期重放通过。五个源码快照、两套冻结输入、本轮两份核心源码及新增测试的SHA256核验全部无漂移；未整体恢复共享工作区其他任务的改动。


## 限制及复核入口

这些来源在此前轮次已出现，嵌套50/100顾客与跨模型比较共享几何；重复进程不是新增的独立实例。固定工作量和固定预算分别回答计算开销与实际求解质量，不能混为同一种收益。全部为描述性观察，不声明显著性、全数据集稳定收益或全局最优。此次缓存没有加强下界，也不能证明最优性GAP下降。

本任务在等时质量实验期间不并行执行回归、profiling或其他CPU基准；共享机器其他负载未完全控制。profiler只用于发现热点，不纳入采用门槛。

统计谬误检查11/11：模型/预算分层、均值不外推、明确来源依赖、不作碰撞变量调整、保留失败分母、不择极端种子、保留所有退步、不以显著性筛选、每候选门槛先登记并披露两次事后子集选择、负载与几何依赖透明、固定工作量与固定预算分开解释。

- [预登记](../../output/experiments/alns_single_batch_repair_geometry_20261001/protocol.json)
- [CPU完整比较](../../output/experiments/alns_single_batch_repair_geometry_20261001/fixed_work_summary.json)
- [CPU门槛判断](../../output/experiments/alns_single_batch_repair_geometry_20261001/primary_decision.json)
- [全部等时作业清单](../../output/experiments/alns_single_batch_repair_geometry_20261001/quality_manifest.json)
- [原候选否决](../../output/experiments/alns_single_batch_repair_geometry_20261001/global_candidate_decision.json)
- [第二候选协议](../../output/experiments/alns_single_batch_repair_geometry_20261001/scoped_protocol.json)
- [第二候选否决](../../output/experiments/alns_single_batch_repair_geometry_20261001/scoped_decision.json)
- [M3确认协议](../../output/experiments/alns_single_batch_repair_geometry_20261001/m3_protocol.json)
- [M3 CPU确认](../../output/experiments/alns_single_batch_repair_geometry_20261001/m3_fixed_work_summary.json)
- [M3全部质量结果](../../output/experiments/alns_single_batch_repair_geometry_20261001/m3_quality_comparison.json)
- [M3逐对结果](../../output/experiments/alns_single_batch_repair_geometry_20261001/m3_quality_pairs.csv)
- [M3规模细分](../../output/experiments/alns_single_batch_repair_geometry_20261001/m3_quality_size_strata.json)
- [最终采用决定](../../output/experiments/alns_single_batch_repair_geometry_20261001/decision.json)
- [最终验证](../../output/experiments/alns_single_batch_repair_geometry_20261001/verification.json)
- [工作区复现](../../output/experiments/alns_single_batch_repair_geometry_20261001/live_equivalence.json)
- [M3最终源码差异](../../output/experiments/alns_single_batch_repair_geometry_20261001/m3_candidate.diff)
- [完整回归](../../output/experiments/alns_single_batch_repair_geometry_20261001/full_regression.log)
- [源码差异](../../output/experiments/alns_single_batch_repair_geometry_20261001/candidate.diff)
- [定向回归](../../output/experiments/alns_single_batch_repair_geometry_20261001/targeted_regression.log)

复算入口为实验目录内的`fixed_compare.py`及`run_pairs.py quality_manifest.json quality after_source`。最终M3复算用`m3_fixed_compare.py`、`run_pairs.py m3_quality_manifest.json m3_quality m3_source`及`check_final.py adopted_source`。使用冻结源码和新的输出目录，避免覆盖原始记录。
