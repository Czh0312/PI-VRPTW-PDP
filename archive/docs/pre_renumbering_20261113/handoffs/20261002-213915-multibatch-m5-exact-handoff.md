# 多批次 M5 精确求解：聊天窗口交接

交接时间：2026-10-02 21:39:15（Asia/Shanghai）  
项目根目录：`/Users/chenzeheng/Desktop/2E-VRPTW-PDD`  
阶段：2026-10-01 晚间至 2026-10-02 早间的优化已经结束，自动续跑 **PAUSED**。本次仅保存交接，不恢复求解或调度。

## 接手者先看什么

1. 本交接：采用状态、证书入口、参数和容易误读的历史记录。
2. [夜间最终报告](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/docs/m5_overnight_20261002.md)：成果、算法联系、收益归因和证据边界。
3. [静态停止检查点](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/checkpoint_at_stop.json)与[当前检查点](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/checkpoint.json)：当前状态及逐段记录。当前 `status=overnight_complete_automation_paused`、`active_process=null`。
4. 按实例读下面的合并证书；需要复现再读[最终冻结源码](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/source_snapshot_phase14)、对应参数、输入和验证器。

最重要结论：50 顾客 gap 从 0.54382355% 降到 **0.00001202035779%**，20/30 顾客也接近保留的数值余量。所有完整方案审计通过，但三例均**没有宣称完整 M5 严格最优**。夜间有效改进主要来自条件物理子问题、逻辑 Benders 式有效切割，以及完整可行上界的车辆/时刻和路线池优化；这些新增实验切割尚未接入公开默认求解流程。

## 用户目标与有效边界

用户希望逐步优化多批次 M5 的精确求解，曾明确提出尝试列生成、拉格朗日等精确方法，并授权只在这一晚持续推进。该晚间安排已结束；后续算法工作须由用户新的明确指令授权，交接不复活已停止的夜间任务。

业务输入及规则保留：原仓数与固定分仓、原容量、时间窗、配送/回收任务、使用和周转时长、回收截止、清洗与箱库存、真实物理车辆数及每车最大趟数。三个冻结实例是 `zhou_Set2_100-5-1b_subset20/30/50_b3` 场景，固定三批次；属于原冻结场景样本，不代表任意规模或其他输入的普遍性能。

全局下界必须来自可核查的完整域松弛/有效必要条件；上界必须是完整 M5 可行日程并通过独立审计。受限候选池目标、固定有序路径模型的 OPT/INF、限时未知，都不能冒充完整问题的全局界、最优或不可行证明。保守数值余量必须保留。

共享目录有其他聊天的重构和实验，工作树大量未提交修改。只定向处理本任务模块或新增文件；保留他人改动、旧源码、旧实验及失败日志。不提交、不推送、不发布、不发送外部消息；未要求并行子代理，不创建子代理。

## 当前最好结果与证书

Gap 定义为 `(UB-LB)/UB`，下面统一显示百分比。

| 冻结实例 | 已复核全局下界 LB | 已审计完整上界 UB | Gap（%） | 权威合并证书 |
|---|---:|---:|---:|---|
| 20 顾客 | 1437.4736320201964 | 1437.473775767574 | 0.00000999999999317 | [master49](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/master49/combined_certificate.json) |
| 30 顾客 | 1623.9783373672776 | 1623.9784997651277 | 0.000010000000007 | [medium25/1](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/medium25/1/combined_certificate.json) |
| 50 顾客 | 2235.8130902493017 | 2235.813359002067 | 0.0000120203577937 | [50 顾客证书](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/combined_certificate.json) |

[最终界与完整方案哈希](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/phase14_final_bounds.json)记录三例 `full_audit_valid=true`、`full_proven_optimal=false`；最终阶段重新审计了全部三份完整方案。

50 初始 LB=2235.4231373718553、UB=2247.646367634466，完整 UB 下降 11.833008632399。UB 中途经历 2238.1568127099035、2235.9725985686455，最终为 2235.813359002067。最终一级成本 1046.9486349700592、二级成本 1188.8647240320079。

50 剩余绝对差 0.00026875276535065495，等于全局保守余量 0.00022358133128363988 与 S1 覆盖余量 0.00004517143401017165 之和，余数约 5.68e-14。20/30 差额分别约 0.0001437473776/0.0001623978501，也处于保守余量尺度。差额核算不授权取消余量；Gurobi 浮点求解 OPT 仍不是完整问题的严格数学最优证明。

30 初始完整 UB=1654.8220896736802，下降 30.84358990855253。20 完整 UB 未改，初始 LB=1426.648954231285，最终提高到 1437.4736320201964。不同实例的提升不得相加。

### 50 顾客证据链

- 全局下界：[下界证书](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/global_lower_newpoint9_certificate.json)、[独立矩阵和界复算](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/newpoint9/independent_validation.json)、[完整域主模型 MPS](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/newpoint9/master/no_good.mps.gz)。原始全局松弛值为 2235.813313830633，扣余量后报告 LB。
- 完整上界：[最好完整方案](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/best_full_result.json)、[上界证书](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/global_upper_pool22_certificate.json)、[独立完整日程/算术/原物理仓与一级库存投影](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/pool22/physical_proposals/validation/summary.json)。
- [最好完整上界投影到认证下界主模型](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/pool22/physical_proposals/validation/best_lower_projection.json)：实际物理成本 epigraph、原列和所有切割触发器逐项核对，通过固定投影。

### 30 顾客证据链

- [最好完整方案](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/medium25/1/best_full_result.json)、[完整审计](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/medium25/1/preserved/validation/summary.json)。
- 原认证下界主模型保留在[旧完整联合 MPS](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_timed_integrality_20261001/verified_medium_180s/01_42_service_models/joint/joint_inventory.mps.gz)，[新上界投影](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/medium25/1/preserved/validation/global_lower_projection.json)通过；未把候选池 OPT 当全局证明。

### 20 顾客证据链

- [最终完整域整数化主模型](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/master49/master.mps.gz)、[独立验证](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/master49/independent_validation.json)。全部 7268 个原完整路线选择仅从 C 改为 B，原界、目标、行与 120 个指示约束保留；共 7747 变量、1136 线性行。原完整方案固定 7739 个已定义变量，0/1 投影通过。
- [S3 单组条件不可行证明](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/core47/independent_validation.json)、[从原局部完整矩阵核查放松](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/site45/original_projection_validation.json)。
- [原完整可行方案](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/medium25/0/inputs/initial_result.json)、[原完整审计](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/medium25/0/control/validation/summary.json)。

## 已完成与真正保留的算法

| 工作 | 保留状态与必要条件 | 入口 |
|---|---|---|
| 条件物理仓子问题 | 私有实验模块；保留原真实车辆串行、所有原仓订单和物理时刻，只放松一级路径及足够大的局部箱池；完整方案必须能投影 | [multibatch_physical_site.py](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/solvers/exact/multibatch_physical_site.py) |
| 条件成本/不可行切割 | 私有实验模块；整数供货阈值、截止前最少/最多回仓数量双向分类，精确任务组匹配与 AND；仅已证明放松 INF 生成 no-good | [multibatch_physical_cuts.py](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/solvers/exact/multibatch_physical_cuts.py) |
| 真实车辆与回仓时刻重排 | 已审计的完整上界改进；保留原有序任务路径，重排真实 E2 车辆/时刻并连接 E1 与清洗库存；局部界仅属受限模型 | [multibatch_vehicle_retime.py](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/solvers/exact/multibatch_vehicle_retime.py) |
| 覆盖路线保留与候选池 | 上界流程有效；每条提案用原有向网络重新计算，忽略提案缓存成本，保留原池完整域；候选池只提供完整可行 UB | [multibatch_cover_pool.py](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/solvers/exact/multibatch_cover_pool.py) |
| 固定有序完整覆盖排程 | 条件诊断/上界用；不能把固定顺序 INF 推广到无序任务组 | [multibatch_cover_schedule.py](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/solvers/exact/multibatch_cover_schedule.py) |
| 全访问顺序分组排程 | 全组内允许排列，保留成本支配路径，连接完整一级供货/回收/清洗库存及全部真实 E2 车辆时钟；保护零时长因果顺序 | [multibatch_group_schedule.py](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/solvers/exact/multibatch_group_schedule.py) |
| 全路径组条件切割与投影 | 只接收与原业务一致、已独立验证的 oracle；固定全组条件后才连接成本下界或 no-good | [multibatch_group_cuts.py](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/solvers/exact/multibatch_group_cuts.py) |
| 任务对/成本上限/子集行 | 诊断工具保留；不等于本轮主要改进已默认启用 | [任务对](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/solvers/exact/multibatch_task_pairs.py)、[物理上限](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/solvers/exact/multibatch_physical_ceiling.py)、[子集行](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/solvers/exact/multibatch_subset_rows.py) |

任务组标志匹配同一精确任务集合的**全部截止列**。实际仓成本变量 theta 同时支配压缩覆盖成本；不能把真实物理日程成本下界直接写到可能不可物理执行的廉价压缩代表列成本上。

50 主要物理下界联系：S3 任务分组与第二截止前最多零箱条件，及其部分组推广。20 最终发现 S3 单组 `(C29_order,p),(C88_order,d),(C88_order,p)` 即已条件不可行，其余 S3 任务自由。局部模型原 1499 变量，只增加 20 分配变量、341 行，关闭 37 条跨组弧，组内弧保留；从原 1823 变量局部完整模型逐项检查只删除一级变量及触及它们的行。局部箱池 33 大于需求 11，未通过缩小库存制造不可行。独立种子 43 再现 INF。

最后只新增一个任务组标志和两行，再对完整域全部路线选择整数化，得到 20 顾客最好下界。分组缩减曾有删除候选超时，未接受该删除，**没有声称最小冲突**。最后 S1 分数列被第一版准备器拒绝；v2 只用精确覆盖且整数的 S3/S4 分组，不能把分数解当完整分组。

早期列生成、完整枚举和联合库存证书仍有代码/历史报告；本夜主要收益不能归因于新增拉格朗日算法。没有完成通用的自动逻辑 Benders 收敛求解器，当前是逐项审计的模块、切割与实验驱动。

## 性能证据与验证边界

耗时对照顺序运行，使用同输入、同初解、相同阶段预算和单线程；独立验证在计时实验后执行。额外数学审计/历史 oracle 成本不冒充同预算算法收益。

| 比较 | 实际观察 | 可得结论与限制 |
|---|---|---|
| 50 固定/可变真实车辆重排 | 种子 42/43/44，各 30 秒；固定不改善，可变均到 UB=2238.1568127099035 | 三种子该冻结例的完整上界收益；不能推广至其他输入 |
| 50 覆盖保留 32 vs 64 池 | 2142 vs 2658 列得到同一最好 UB；实测 7.35 vs 6.92 秒 | 少 19.41% 列，实测没有加速 |
| 30 普通池 vs 保留覆盖池 | 同 90 秒、种子 42；1456/1542 列，UB=1636.042837733466/1623.9784997651277；1.492/2.507 秒 | 完整上界改善，未加速；参数见下 |
| 20 普通池 vs 保留覆盖池 | 920/956 列均 UB=1437.473775767574；约 0.99/6.43 秒 | 无上界收益且更慢 |
| 20 master49 vs control50 | 同 90 秒主模型预算、种子 42、线程 1；处理 raw LB=1437.473775767574，控制 raw LB=1432.3072816144345 | 只删除最终 S3 单组 no-good 的配对矩阵核查通过，差 5.166494153139411；历史 oracle 另记账，不声称从零总耗时/加速 |
| 20 control51 | 原完整联合域仅全部列整数化、无今晚条件切割；raw LB=1426.8744831916165 | 整数化本身不足以消除剩余物理 gap |

[配对矩阵核查](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/phase14_paired_cut_validation.json)、[control50 字段澄清](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/control50/isolated_control_clarification.json)、[control51 独立验证](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/control51/independent_validation.json)是归因入口。**control50 的 `candidate_global_lower` 继承外部旧最好界 1434.354707721595，不能当作隔离控制结果；必须使用本模型 raw bound。** master49/50/51 主模型阶段实测约 0.54/0.56/0.56 秒，参数中的 historical oracle charge 和独立验证需单独说明。

相关不同回归累计 404 项，最终定向组合 125 项通过（1.59 秒，其中 67 项新增全路径/切割相关测试）；另对 7 份已冻结、独立完整最优的小例验证完整列整数化下界与完整方案 0/1 投影，见[小例验证](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/small_integrality52/validation.json)。7 份投影复核不能计作新增 pytest 数，也不声称重跑了整个共享路由测试集。

大例下界经原矩阵/目标/指示约束核查、独立种子复算；完整上界检查有向成本、配送/回收精确覆盖、使用时长、真实车辆准备/串行/周转、原物理仓及一级截止库存固定投影。测试通过不是性能提升证明；三例仍保留 `full_proven_optimal=false`、`full_proven_infeasible=false`。

## 尝试过但未采用或不能推广的方案

| 尝试 | 最终状态及原因 |
|---|---|
| 无完整任务分组的 S1/S3 原物理弧 oracle | 240/180 秒弱界或限时未知，没有新可靠证据；不生成 INF 切割 |
| 固定有序覆盖 cover26 的 INF | 只属于该顺序，不能直接排除无序任务组；后来 group27 才检查全部允许排列 |
| 扩大槽位/候选、重复固定路径时刻优化 | 多轮无 UB 改善；业务每车趟数上限和真实车辆数未增加 |
| 相同候选的八条额外物理提案 | 已在候选池中，两组都得到同一最好 UB；不能归因于提案增量 |
| service_envelopes | 两种子仅小收益，第三种子限时退步，仍默认 False；[服务包络报告](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/docs/m5_route_service_20261001.md)保留 |
| 三任务子集 16 行、S1 供货/成本地板 | 未提高最好界；site40→master42 不重复求解已最优的同一松弛 |
| 分组缩减中超时的删除候选 | UNKNOWN 不接受；不声称最小冲突 |
| 第一版准备器将 S1 分数列构造整数组 | 已拒绝，失败源码保留，改用 v2 的整数 S3/S4；勿按旧版直接执行 |

参数调用、暖启动供货上下文、GPInfinity、目录/验证字段等失败版本及日志保留，再用新版本修复；不能删除失败来美化过程。

## 源码、冻结输入与运行现场

本次只读核查确认下列十个本工作新增模块与 `source_snapshot_phase14/models/routing/solvers/exact/` 同名文件 SHA 一致：physical_site、physical_cuts、vehicle_retime、task_pairs、physical_ceiling、subset_rows、cover_pool、cover_schedule、group_schedule、group_cuts。

当前公开接口已拆到 [M5 exact/solver.py](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/variants/model_5/exact/solver.py)、[exact/bounds.py](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/variants/model_5/exact/bounds.py)和[接口说明](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/variants/model_5/exact/README.md)。旧单文件 `models/routing/variants/model_5/exact.py` 已不存在，不能按旧路径执行。`bounds.joint_route_inventory_certificate` 转到 [multibatch_joint_inventory.py](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/solvers/exact/multibatch_joint_inventory.py)；**当前函数默认 `timing_envelopes=False, service_envelopes=False`**，时间包络须显式开启。夜间认证继承此前已验证的时间/服务模型，但不据此更改公开默认开关。新增物理/组切割没有默认公开集成，当前其他聊天的混合求解入口不等同于本夜冻结流程。

| 场景 | 冻结业务输入 | 初解/最好上界 | 参数/驱动 |
|---|---|---|---|
| 50 | [case.json](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/inputs/case.json)及同目录 data.csv、orders.json、fleet.json、dataset_metadata.json | [原初解](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/inputs/initial_result.json)、[最好方案](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/best_full_result.json) | [pool22 参数](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/pool22/parameters.json)；下界步骤见 newpoint9 与冻结驱动 |
| 30 | [case_relocated.json](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/medium25/1/inputs/case_relocated.json)及同目录原数据 | [原初解](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/medium25/1/inputs/initial_result.json)、[最好方案](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/medium25/1/best_full_result.json) | [同预算参数](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/medium25/1/parameters.json) |
| 20 | [case_relocated.json](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/medium25/0/inputs/case_relocated.json)及同目录原数据 | [原完整初解/最好上界](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/medium25/0/inputs/initial_result.json) | [master49 参数](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/master49/parameters.json)、[control50](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/control50/parameters.json)、[control51](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/control51/parameters.json) |

50 pool22：`time_limit=90, seed=42, max_columns=64, transfers=True, exchanges=True, recombinations=True, extra_slots_per_vehicle=1, max_passes=1, additional_columns_per_slot=64`，受限物理路线池只提供完整 UB。

30：`time_limit=90, seed=42, max_columns=64, transfers=True, exchanges=True, recombinations=False, additional_columns_per_slot=32, extra_slots_per_vehicle=1`；同输入、同初解、同预算、顺序对照。

20 master49/control50：90 秒主模型阶段、种子 42、单线程、完整原 7268 列、相同初解与旧切割；参数记录已发生的 `oracle_time_charged=3.3904795420000013`，不代表此次从零统一流程耗时。control51 同 90 秒/42/单线程，无夜间条件切割。

输入的原循环起止 480/1260 分钟，离散 tick=1 秒；三批配送时长 180/180/225、回收时长均 60；清洗 15 分钟、一级装载 2、二级准备/回仓卸载/库卸载各 1，E1 周转 0、E2 周转 1、每车最多 12 趟，终态全部清洁于中心库。箱池 20/30/50 原值分别 33/47/77。具体车队容量与订单时窗以冻结 fleet/orders 为准，不用交接文字替代业务输入。50 case 的五个外部文件引用在本次只读核查中均存在（pool_config 仍指向项目公共配置，未来复现要核对冻结配置与清单）。

夜间使用项目 `.venv/bin/python`（当时 Python 3.9）、Gurobi 12.0.3。完整源码、测试源、配置和驱动：[source_snapshot_phase14](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/source_snapshot_phase14)；末段驱动实际在其内部 [experiment_drivers_phase14](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/source_snapshot_phase14/experiment_drivers_phase14)，含 master49.py、control50.py、control51.py、prepare_phase14_v2.py 与独立验证器。不要误找实验根目录下不存在的同名驱动目录。

驱动会创建专用目录并改写检查点，**不能盲目原地重跑**；未来授权的新实验应新版本、新输出目录，沿用冻结输入/初解、记录单一总预算，把枚举、oracle、主模型、验证成本分开。先检查是否已有本任务活跃进程，避免重复启动。

### 关键 SHA-256（本次重新只读核对通过）

| 文件 | SHA-256 |
|---|---|
| 50 最好完整方案 | `7ffb79e95bcec3953544f9c0cc8298dc72bb429168c15d3659f731b63ed97ea1` |
| 50 newpoint9 主模型 | `ef468f1f269b5f81390e7bed1a4bdc1638e5629120ceedf09fc6b40f0948d1b3` |
| 30 最好完整方案 | `915d61f2191f2c1d308780f00905e1dceeb7a3717968c4210a075c8d691c39fc` |
| 30 原认证下界主模型 | `31c4f01be486221f6e542903242a168bb299c915e83395b8f4db624c4d671210` |
| 20 完整上界 | `e9de8a5708699b9529ea8c940554666a7aa593c120a1a4dbad9c35dba2cb24e9` |
| 20 master49 主模型 | `3fc0344e7b2de9cd1eed05c9fbdddc589ce62b69242804a0decc952327549242` |

最终[全部清单核查结果](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/overnight_all_manifests_rechecked.json)记录 7268 条清单记录、5188 个不同文件、0 失败（这是停止清单生成前的夜间复核计数，不包括本交接）。[末段冻结清单](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/manifest_phase14_final.json)和[停止清单](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/manifest_overnight_stop.json)保留；本次只读重核停止清单中五个文件哈希均通过，没有运行会写入的验证器，也未再次扫描全部 5188 文件。

### 已停止的续跑

自动化 id=`m5`，名称“ M5 多批次精确求解今晚优化 ”，本聊天 id=`01a0f2c3-4ddb-7671-9dad-02d76ca91160`。停止记录时间为北京时间 **2026-10-02 07:06:04**，静态检查点封存于 **07:06:06**；没有建立跨日重复任务。

[停止状态记录](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/automation_paused_at_stop.json)、[停止清单](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/manifest_overnight_stop.json)记载 `PAUSED`、全部本任务 worker 封存前退出；检查点 `active_process=null`。本次交接再次读取 `/Users/chenzeheng/.codex/automations/m5/automation.toml`，状态仍为 **PAUSED**。本次未启动任何 worker。当前 sandbox 不允许 `ps`，因此没有宣称在交接时重新完成全系统进程核查；无本任务活动作业的依据是停止记录与检查点，不推断其他聊天的运行状态。

## 不要重复、覆盖或误读的记录

- 不恢复 m5 夜间调度，不创建跨日安排；已经最优的不变松弛不再重求。总结请求只修改本交接和 project 索引。
- `checkpoint.last_verified_experimental_master` 仍指 subset19，是历史实验指针；50 最好下界用合并证书指向的 **newpoint9/master/no_good.mps.gz**，不能把 subset19 当最新权威界。
- 原冻结失败准备器、失败日志及旧快照不修补覆盖。第一版 prepare_phase14 被拒绝后只使用 v2；分数列禁止当整数组。
- [phase12 指示数勘误](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/phase12_notes_errata.md)：group27 新池 1054 指示加原 E1 120，共 1174；早期原笔记保持原样。
- phase13 曾在收尾 stdout 尚变化时冻错哈希，失败尝试保留；正确入口是 [phase13 收尾说明](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/phase13_finalization_note.md)和 manifest_phase13_final.json，诊断文件 phase13_manifest_attempt2.json 不当权威清单。
- `manifest_s1full16_input.json` 曾冻结旧根 best_full_result；现根文件已提升为 pool22。旧输入保留在 [best_full_result_pre_pool22.json](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/best_full_result_pre_pool22.json)，SHA=`a6d578fef48119ab799a539c812cdc2ebcaf8d58739ccf5319e8e6041399e7ef`，历史别名见 [phase9_historical_input_archives.json](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/phase9_historical_input_archives.json)。不要用现根文件去“修正”旧清单。
- `verify_overnight_final.py` 会更新停止记录、检查点和清单；仅总结或只读核查时不要执行。
- 切割必须覆盖对应条件的完整原业务域；UNKNOWN 不生成 no-good，固定访问顺序失败不推广到全部排列。保留 theta 实际成本连接和完整方案投影检查。
- 项目其他主题的交接、文件整理、公开入口改动和实验不属于本窗口的成果，也不能回滚。当前没有可把整棵 dirty tree 直接提交的干净变更集。

## 未解决事项与建议第一步

以下仅是未来获新指令后的候选工作，本交接不启动：

1. 把已审计的物理/全路径分组 oracle、条件切割、冲突缩减和完整域整数主模型整合成可恢复、明确总预算的通用流程。先读取冻结驱动及矩阵验证器，梳理接口与当前公开目录变化；不重跑最好三例的不变松弛。
2. 扩展不同原业务输入与种子的顺序同预算对照，从同一审计初解出发。完整记录 oracle、枚举、主模型、验证耗时，检验收益是否能迁移，再决定公开集成开关。
3. 若目标是严格最优，研究更强的可核查数值/数学证书；当前小差额主要来自保守余量，不能取消余量来宣称最优。不要把受限物理 oracle 的 OPT 当完整 M5 证明。
4. 对供货与真实车辆串行/回收连接继续提炼更小有效条件，但只接受完整放松的独立 INF 证据；删除搜索 UNKNOWN 必须保留条件，不声称最小冲突。

没有待用户处理的实验故障或必须继续运行的作业；主要遗留是通用集成、跨实例证据及严格证明。

## 新窗口开场指令

```text
当前项目是 /Users/chenzeheng/Desktop/2E-VRPTW-PDD。
请先读 /Users/chenzeheng/Desktop/2E-VRPTW-PDD/project/20261002-213915-multibatch-m5-exact-handoff.md，再读 /Users/chenzeheng/Desktop/2E-VRPTW-PDD/docs/m5_overnight_20261002.md 和 /Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/checkpoint_at_stop.json，并核对三例的合并证书。
当前 m5 夜间续跑已结束且 PAUSED，停止检查点没有本任务活跃 worker；这次交接不授权恢复自动化或启动长时求解。先复述最好上下界、未接入默认入口的模块、证据限制和后续候选工作，等我的下一步明确指令。
保留原全部业务输入与真实车辆/时间/清洗库存规则；不把受限模型的目标或 OPT/INF 当成完整 M5 证明。只在可核查新证书或完整审计可行方案出现后提升最好记录。
共享工作区有其他聊天改动，冻结输入、源码、证书、失败日志和其他主题交接均保留；不提交、推送、发布、外部消息或主动创建子代理。若获准做新实验，用新版本和新输出目录，先检查已有进程，同输入/初解/总预算的耗时对照顺序运行。
```
