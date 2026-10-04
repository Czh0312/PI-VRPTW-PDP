# 单批次 ALNS：四项续试与撤回记录（2026-10-01）

## Material Passport

- Origin Skill：academic-research-suite / experiment-agent。
- Origin Mode：validate，用户授权继续尝试优化单批次 M1–M4。
- Verification Status：VERIFIED，四候选已撤回；恢复版本完整回归、四模型复现、源码及输入哈希全部通过。

## 本轮决定

本轮没有新增采用的优化。四个候选均未满足预登记的两个来源 CPU 中位耗时各至少下降 2% 的门槛，完整撤回。此前已采用的 M3 按需回收插入前缀、准备评分、几何缓存、静态配送载荷汇总及下降搜索继续保留。仅恢复本轮拥有且 SHA256 未变的核心文件，没有整体恢复共享工作区其他任务的修改。

每个版本在计时前单独冻结基线、输入和候选，并登记门槛。两个 100 顾客来源均为 Zhou Set2 5-2/10-1、scenario01，固定 500 迭代、60 秒不触发上限；每版本各三个独立进程重复，前后交替串行，共 12 次。固定迭代的路线、分仓、距离、迭代数必须完全相同，全部独立审计/周期重放有效，且两来源各至少提速 2%，才执行质量比较。

CPU 减少为 `100 × (1 − 候选 CPU 中位 / 基线 CPU 中位)`；负值表示变慢。

| 候选 | 5-2，100 顾客 | 10-1，100 顾客 | 决定 |
|---|---:|---:|---|
| 配送插入前缀 | -1.68% | -7.61% | 撤回 |
| 固定任务序列容量检查缓存 | +4.30% | -4.47% | 撤回 |
| 直接构建整数编号时间图 | +1.96% | +5.17% | 撤回 |
| 整数编号时间图 + 按需更新时间 | +1.40% | +9.32% | 撤回 |

48 次正式固定工作量运行全部逐值复现，独立审计和周期重放全部有效。每个版本的 8 对等时质量作业均因 CPU 门槛失败取消，共 32 对登记作业取消、0 对执行。不能据这些 CPU 结果推断等时解质量或最优性 GAP 改善。

## 实现与检查

1. **配送插入前缀，CPU seed121。** 新配送会改变初始有序重量/体积求和，因此先按候选配送位置保持原 `sum` 顺序，再按插入订单、数值与类型完全一致的初始载重分开复用前缀。新增配送即使插入纯回收路线，也重新计入供货等待和出车准备。只对 M3、精确类型的准备评分器开启，默认关闭。29 项新增检查与既有修复/评分检查共 149 项通过。保留两轮测试夹具失败：使用了不存在的 o3；构造非结合载重时忽略了容器重量，实际小载荷为 2 而非 1。修正夹具后所有位置的评分和完成时间与两条原路径逐值一致。

2. **容量检查缓存，CPU seed131。** 固定任务序列和容量确定初始及每事件的原始容量布尔检查，以 LRU512 缓存；有序求和、逐事件加减、负载下限、容量阈值和 1e-6 容差均不变。容量值进入键，未知任务返回原解码路径，保持错误优先级。时间图、供货、车辆衔接、库存、覆盖和审计全部重建。31 项新增检查，联合 131 项通过。一次既有 M1 构造器 spy 不接受新关键字导致测试失败，随后恢复 M1/M2/M4 原构造接口，仅 M3 传新参数。一次误启动的预检查因候选尚未冻结，6 个候选进程失败；另有 6 次基线运行，全部原始记录独立保留，不纳入 48 次正式配对运行，也不据它们筛选性能。

3. **直接构建时间图，CPU seed141。** 每次重新构建全部节点、边、上下界，只按原节点插入顺序直接分配整数编号，省去 tuple/dict 到 dense list 的转换。队列仍为原 LIFO，子边顺序和 `max`/加法完全不变；起点默认周期上界、结束卸货/周转、同车多趟与跨路线配送回收前序均相同。55 项新增检查，联合 155 项通过。第一来源仅 1.9646798%，严格不足 2%，没有四舍五入为通过。

4. **时间图第二版，CPU seed151。** 在第三版基础上，将新函数中的 `max(旧值, 就绪时间+边时长)` 改为只在新值严格更大时写回。相等时保持旧值，与两参数 `max` 一致；另用 signed-zero、相等、NaN、正负无穷反例验证传播行为。原 tuple-to-dense 函数未修改，M1/M2/M4/M5 默认不启用新表示。63 项新增检查，联合 163 项通过。第一来源仅 1.4042323%，仍撤回。第四版是在第三版失败后登记的明确新实现，原失败决定保留，没有为同一实测候选重抽种子或下调门槛。

四个候选都覆盖完整解码/修复、随机移动、错误优先级、分数及非结合浮点载荷、供货与等待、时间窗、同车复用、任务覆盖及自定义/默认入口范围。图版本额外覆盖重复边、零时长环、部分有环图的更新顺序、空路线造成的输入索引间隙和跨路线前序。

## 恢复版本验证

恢复后完整回归为 **1641 passed, 14 warnings in 101.88s**，警告来自依赖库弃用接口。测试数量包含共享工作区其他任务的检查；本轮新候选测试全部移至实验归档，没有作为已采用代码或活动测试保留。

两个 100 顾客来源，公开 M1–M4，seed151、固定 500 迭代的八组，基线/冻结恢复版/工作区共 24 次独立求解的路线、分仓、目标值与迭代数完全一致，独立审计及周期重放全部有效。

四个实验的前后源码、冻结输入、撤回测试和恢复文件 SHA256 全部无漂移。最终冻结恢复源码与本轮基线逐字节相同，工作区四个恢复核心文件与基线相同；此前采用的回收前缀实验的三个源码快照、输入及工作区拥有文件/测试哈希均保持原值。没有合入四项已撤回实现。

- [恢复版完整回归](../output/experiments/alns_single_batch_direct_relax_20261001/full_regression.log)
- [八组四模型复现](../output/experiments/alns_single_batch_direct_relax_20261001/live_equivalence.json)
- [全部最终核验](../output/experiments/alns_single_batch_direct_relax_20261001/final_verification.json)

## 限制与统计检查

几何、母来源和场景已在此前优化中出现；质量清单中的 50/100 顾客嵌套，新种子不代表新的独立实例。四个候选依次探索，第二版根据第三版观察选出，需将其视为保留筛选过程的探索性结果。每版独立进程重复只用于观察计时波动，不能扩大实例分母。

CPU 计时不包含加载和求解后的额外审计，profiling 单独执行且仅用于选方向。本任务的 profiling、测试和正式 CPU 比较全部串行；没有执行本轮等时质量试验。共享机器其他负载、频率和温度未完全控制，有限重复不足以宣称稳定普遍收益。严格门槛失败只决定本次不采用，不证明这些方向在其他实例都无效。

不宣称显著性、全局最优、等时质量改善或最优性 GAP 缩小；没有加强下界。统计谬误检查 11/11：两来源分别判断、公开来源依赖、重复不扩大分母、不选择极端种子、正式/误启动/取消作业分开、不删失败结果、不作因果或显著性推断、公开四版筛选过程、门槛先登记且未修改、披露计时负载、CPU与质量/GAP分开解释。

- 配送插入前缀：[预登记](../output/experiments/alns_single_batch_delivery_prefix_20261001/protocol.json)、[全部 CPU 原始数据](../output/experiments/alns_single_batch_delivery_prefix_20261001/fixed_work_runs.json)、[门槛](../output/experiments/alns_single_batch_delivery_prefix_20261001/primary_decision.json)、[决定](../output/experiments/alns_single_batch_delivery_prefix_20261001/decision.json)、[源码差异](../output/experiments/alns_single_batch_delivery_prefix_20261001/candidate.diff)、[哈希核验](../output/experiments/alns_single_batch_delivery_prefix_20261001/verification.json)。
- 固定任务序列容量检查缓存：[预登记](../output/experiments/alns_single_batch_event_capacity_20261001/protocol.json)、[全部 CPU 原始数据](../output/experiments/alns_single_batch_event_capacity_20261001/fixed_work_runs.json)、[门槛](../output/experiments/alns_single_batch_event_capacity_20261001/primary_decision.json)、[决定](../output/experiments/alns_single_batch_event_capacity_20261001/decision.json)、[源码差异](../output/experiments/alns_single_batch_event_capacity_20261001/candidate.diff)、[哈希核验](../output/experiments/alns_single_batch_event_capacity_20261001/verification.json)。
- 直接构建整数编号时间图：[预登记](../output/experiments/alns_single_batch_direct_graph_20261001/protocol.json)、[全部 CPU 原始数据](../output/experiments/alns_single_batch_direct_graph_20261001/fixed_work_runs.json)、[门槛](../output/experiments/alns_single_batch_direct_graph_20261001/primary_decision.json)、[决定](../output/experiments/alns_single_batch_direct_graph_20261001/decision.json)、[源码差异](../output/experiments/alns_single_batch_direct_graph_20261001/candidate.diff)、[哈希核验](../output/experiments/alns_single_batch_direct_graph_20261001/verification.json)。
- 整数编号时间图 + 按需更新时间：[预登记](../output/experiments/alns_single_batch_direct_relax_20261001/protocol.json)、[全部 CPU 原始数据](../output/experiments/alns_single_batch_direct_relax_20261001/fixed_work_runs.json)、[门槛](../output/experiments/alns_single_batch_direct_relax_20261001/primary_decision.json)、[决定](../output/experiments/alns_single_batch_direct_relax_20261001/decision.json)、[源码差异](../output/experiments/alns_single_batch_direct_relax_20261001/candidate.diff)、[哈希核验](../output/experiments/alns_single_batch_direct_relax_20261001/verification.json)。
