**M5 多批次有限容器池循环系统**

实现日期：2026-09-29。策略：`multi_batch_cutoff_v1`，案例和结果 schema：2。
本版实现串行一级批次、二级跨批回收、有限容器周转、周期末闭合，以及联合精确求解和独立 ALNS。
设计依据见 [完整实施计划](multibatch_container_cycle_plan.md)。

本轮确认的静态信息条件、固定订单分批、单批/多批对照和后续数据工作，见 [静态研究计划](static_single_multibatch_study_plan.md)。该计划区分已有功能和待开展的正式实验。

静态研究执行链路已补齐：必要条件与容器池下界检查、前置仓服务时间读取、长周期初解、
冻结输入/源码的池规模及清洗时长扫描，以及区分证明状态的汇总。
新增命令和结果口径见 [静态多批次代码说明](static_multibatch_implementation.md)。
2026-09-30 已加入多批次距离下降邻域与稳定初始搜索；50/100 顾客、有限池、多种子的
同预算改善与退步均见 [M5 ALNS 优化记录](model5_alns_optimization_20260930.md)。
2026-10-01 进一步复用相同一级回收方案和未变化仓点的二级候选，见
[第二轮优化与新母例对照](model5_alns_round2_20261001.md)。
同日测试了停滞重启、退火周期和探索分支下降；完整结果退步，已撤回，见
[第三轮搜索与预算对照](model5_alns_round3_20261001.md)。
第四轮的有向弧缓存加快枚举，但同预算质量与稳定性未改善，现仅作为可选实验接口，见
[第四轮完整记录](model5_alns_round4_20261001.md)。
第五轮修正旧车辆时刻误筛：原候选队列穷尽后，使用新车辆前缀补查任务迁移。
完整平均距离和平均 CV 门槛通过，已作为默认下降策略采用；分组退步仍保留，见
[第五轮采用及局限](model5_alns_round5_20261001.md)。
第六轮的三种后悔值修复均未达到开发门槛，未替换默认算法；整趟/多订单重组方向见
[第六轮试验记录](model5_alns_round6_20261001.md)。

**可以直接运行的入口**

在项目根目录执行；下面使用项目现有虚拟环境，其他环境可将 `.venv/bin/python` 换成相应 Python。

```sh
# 多批次 ALNS，无需 Gurobi
.venv/bin/python main.py route --case config/cases/multibatch_demo.json --solver alns --time 10

# 完整联合 MILP，需要 Gurobi 和有效许可证
.venv/bin/python main.py route --case config/cases/multibatch_demo.json --solver exact --exact-time 60

# 容器池 2/3 箱，精确参考 + ALNS 三个随机种子；失败也保留
.venv/bin/python experiments/runs/run_multibatch_cycle.py --pool-sizes 2 3 --seeds 42 43 44 --time 1 --exact-time 10

# 本版机制回归；无 Gurobi 时只跳过精确求解测试
.venv/bin/python -m pytest tests/routing/test_multibatch_cycle.py -q
```

默认输出到 `output/single_runs/multi_batch_cutoff_v1/时间戳_算法/`；批量结果位于 `output/experiments/multi_batch_cutoff_v1/`。也可传 `--output`，目标目录必须不存在或为空，防止覆盖结果。

**实际执行的时间规则**

对每个批次定义 `start`（领箱、装车开始）、`cutoff`（配送阶段结束、冻结空箱资格）、`end`（一级回总仓并卸箱的期限）。下一批从上一批 `end` 开始；配送和回收时长都来自配置。

```text
start_b ── 装车 + 一级配送 ── cutoff_b ── 一级取箱 + 回总仓卸箱 ── end_b
                                                                    = start_(b+1)
```

一级车必须在 `cutoff` 前完成该批前置仓卸货。驶向首个取箱站的行程也在回收阶段内：混合路线在最后配送点等待到 `cutoff` 后再前往取箱点，纯取路线不早于 `cutoff` 从总仓出发。混合路线无需在两阶段之间回总仓。

二级卸箱完成时间 `u` 决定回收归属。初始前置仓空箱为零，且每轮全部取走冻结量，所以：

```text
q(s,1) = Σ 本仓在 u ≤ cutoff_1 时卸入的箱数
q(s,b) = Σ 本仓在 cutoff_(b-1) < u ≤ cutoff_b 时卸入的箱数
```

`q` 是必须取走的等式数量。二级车已到仓但未卸完不计入；恰好截止完成卸箱可以计入；截止后卸箱必须等下一轮，一级车稍晚到仓不扩大本轮资格。`q=0` 不创建空访问，`q>0` 由一辆一级车一次取完。如果容量或回收阶段长度不够，该方案不可行。

每批在 `start` 一次领取该批全部需求箱数。只有总仓已卸箱、再经过 `cleaning_duration` 的箱子才能重新领用；清洗恰好在领箱时完成可以使用。不能按批次补满容器池。

最后一批带箱二级趟次必须在最后 `cutoff` 前卸完箱；一级车须在最后 `end` 前回仓卸完；全部容器须在 `cycle_end` 前清洁可用。最后 `end` 到 `cycle_end` 可以留给清洗与车辆整备，不能暗中增加一轮取箱。审计将同一时间表平移一个周期连续重放两次，第二周期继承第一周期的实际库存。

这支持无损耗、确定性订单和参数按周期重复时的持续循环。设置单个批次，并使唯一截止足够晚，即是同一新框架下的单批闭环。历史 `single_day_backhaul_v1` 的局部收齐即取规则仍保留，因此不要求两个不同策略的最优距离或等待量无条件相等。

**输入和可调整参数**

入口案例是 [multibatch_demo.json](../config/cases/multibatch_demo.json)，关联四类数据：

| 文件 | 修改用途 |
| --- | --- |
| [multibatch_policy.json](../config/routing/validation/multibatch_policy.json) | 批次数、各阶段长度、周期末、容器池、清洗和整备时间、时间精度、二级趟次上限 |
| [multibatch_fleet.json](../config/routing/validation/multibatch_fleet.json) | 物理车辆数、重量和体积容量、单箱重量/体积、速度 |
| [network.csv](../data/source_inputs/mechanism/network.csv) | 总仓、前置仓、顾客的物理地点；节点 ID 唯一 |
| [orders.json](../data/source_inputs/mechanism/orders.json) | 独立订单 ID、地点、分仓、配送批次、箱数、货重和配送/回收时间窗 |

同一顾客可有多笔订单，不能在物理节点 CSV 中复制顾客行。订单表也支持 CSV；订单字段为：

```text
order_id, customer_id, satellite_id, delivery_batch_id,
container_count, goods_weight,
delivery_ready, delivery_due, pickup_ready, pickup_due, service_time
可选：min_use_time（默认 0），pickup_container_count（必须等于 container_count）
```

`goods_weight` 是该订单货物总重；配送载重另加容器自重。`service_time` 分别用于一次配送、一次回收，时间窗约束服务开始；回收开始必须晚于配送服务完成加 `min_use_time`。前置仓的装卸服务时长来自网络 CSV 的 `service_time`。

周期配置必须显式提供 `cycle_start`、`cycle_end`、`batches`、`container_pool_size`、`cleaning_duration`、`e2_max_trips_per_vehicle` 和 `time_tick_seconds`。每个批次恰好有 `id`、`delivery_duration`、`return_duration` 三项；目前不提供隐式 B/D/R 快捷展开。

| 作业时长 | 实现口径 |
| --- | --- |
| `e1_load_time` | 从一级批次开始领箱到发车，计入配送阶段和车辆占用 |
| `e2_dispatch_prep_time` | 二级带配送任务的趟次准备/装货时间；货物到仓且物理车可用后才能开始。纯取趟次不加此项 |
| `e2_return_unload_time` | 二级回前置仓后卸箱；结果的 `return_complete` 已包括它 |
| `depot_return_unload_time` | 一级回总仓后的卸箱；结果的 `return_complete` 已包括它 |
| `e1_turnaround_time`、`e2_turnaround_time` | 回仓卸箱后到该车再次可用的整备时间；周期边界也检查 |
| `cleaning_duration` | 一级卸箱完成到清洁可用；与车辆整备可并行 |

这些时间与节点服务时长应按实际业务分别设定，避免重复计时。示例参数均为情景假设。可在案例中显式指定 `distance_matrix` 和 `duration_matrix`，后者需要前者；否则使用既有网络距离和速度机制。

**时间单位与结果契约**

输入时间统一为分钟。内部采用整数 ticks，精度由 `time_tick_seconds` 指定，示例为 1 秒。周期起止及阶段长度必须恰为刻度整数倍；行驶/作业时间和窗口下界向上取整，窗口上界向下取整。距离目标使用原始距离。

`result.json` 的所有时间均为 ticks，带有 `time_unit` 和 `time_tick_seconds`；换算为分钟使用 `ticks × time_tick_seconds / 60`。图中的时间轴为分钟。此序列化约定保证保存、加载后不会因浮点舍入改变截止归属。

| 输出 | 内容 |
| --- | --- |
| `result.json` | 日程、E1/E2 物理车和趟次、实际发车/服务/卸箱时间、冻结量、清洁释放、独立审计与求解状态 |
| `manifest.json` | 生效配置、输入和源码哈希、Python 版本、转换后订单时间窗/作业时间/行驶时间 |
| `inputs/`、`source_snapshot/` | 本次输入文件和求解入口源码快照 |
| `solver.log`、`report.md` | 求解日志、状态和逐批清洁库存；异常另有 `error.json` |
| `timeline.png` | 总仓清洁库存、每趟车辆占用、各前置仓空箱库存；虚线为截止 |
| `routes_*.png` | 分批路线图；E1 按所属批次，E2 按发车所在阶段归图；每页最多四批 |

时间轴图的横条从发车绘到卸箱完成，车号和趟次可从标签识别；装车准备及整备的冲突由审计另行检查。E2 横条跨过批次边界是允许的，不表示新批次自动增加车辆。

`audit` 重新核对任务覆盖、路线容量、时间窗、先送后取、两级交接、车辆占用、截止等式、各批供箱和周期末态。它验证保存的实际时间表，不擅自改成最早时间表。零时长情况下也通过因果顺序检查防止先取后送。

`container_ledger` 区分清洁、正向占用、顾客、二级空箱在途、各仓空箱、一级空箱在途、脏箱；正向装货/运输环节合并统计。`minimum_pool_for_schedule` 是**当前固定时间表所需的最小池大小**，不能当作整个问题已经优化证明的最小 N。

**算法范围与限制**

精确后端 `multibatch_indexed` 联合决定两级路线、物理车辆、二级趟次、服务和返回时间、各仓每轮取箱量，以及清洁释放。截止分类采用双向约束，不能将本轮已入仓箱子任意推给下一轮。只有完整 MILP 返回最优且独立审计通过才标记 `proven_optimal=true`；证明范围包含配置中的时间刻度和二级趟次上限。

ALNS 在全周期状态上搜索订单任务移动、成对移动、拆合趟次、二级车辆/趟次顺序、跨截止等待、一级配送顺序/分车和取箱安排。每次变动完整重算下游取箱量、回运和清洁库存，没有使用旧单日缓存。输出必须通过独立审计，`HEURISTIC_NO_SOLUTION` 只表示预算内未找到解，不等于已证明不可行。当前是可运行的启发式基线，大规模效果、专门缺箱修复和算子消融仍需后续实验。

当前多批次模型编号为 M5：固定分仓、固定订单配送批次、单一同质容器、无损耗。每仓每批的一级配送和回收各不可拆分；每辆一级车每批最多一趟；二级可多趟，每趟最多装一个配送批次的货，但可回收此前订单。前置仓库容、月台和并行清洗能力暂不限制。配送阶段和回收阶段长度是输入，不在当前模型中优化。

**费用后处理**

费用入口只接受通过再次审计的已保存结果。将下面路径替换为一次运行的 `result.json`：

```sh
.venv/bin/python main.py cost --case config/cases/multibatch_demo.json --route-result output/single_runs/multi_batch_cutoff_v1/你的运行目录/result.json --economic-config config/cost/cases/multibatch_synthetic.json
```

费用包括距离、车辆工作时间、启用物理车辆、实际清洗箱次及使用箱次。初始容器投资 `N × 单箱价格` 与周期运营费用分列，可显式选择按周期摊销；不能每批重买整个池。此处只是距离方案的费用评价，没有声明费用最优。[示例费用参数](../config/cost/cases/multibatch_synthetic.json) 使用虚构单位 `synthetic_units`，不能作为真实经营费用或一次性包装比较结论。

**本次验证及后续工作**

机制测试覆盖截止前/恰好截止/截止后、卸箱完成、必须全取、清洗未完缺箱、车辆准备与复用、末批迟回、零时长因果、单批退化、JSON 重载、无 Gurobi ALNS，以及三批两辆一级车循环。2026-09-29 本机全仓库回归为 **253 passed**，其中本版 53 项；Gurobi 精确测试实际执行，未跳过。另有 14 条既有 Matplotlib/PyParsing 弃用警告。

双仓四订单示例的 [最终批量结果](../output/experiments/multi_batch_cutoff_v1/validation_final_20260929/report.md) 包含 N=2/3、精确参考及 ALNS 三个种子。N=2 已证明不可行；N=3 的精确最优距离为约 40.970563，三个 ALNS 解达到同一距离并通过连续两周期重放。该结果只验证机制与小例，不代表 100/200 顾客性能或实际场景验证完成。

下一步按研究目标构造固定总订单的批次数/阶段长度/池规模对照，再做 Zhou/LaDe 路网上的多种子实验。核心功能已经可用，正式算法评测、真实费用校准和论文写作仍在后续研究范围内。

**代码位置**

M5 的定义和求解入口位于 `models/routing/variants/model_5/`；精确实现位于 `models/routing/solvers/exact/`，ALNS 实现位于 `models/routing/solvers/alns/`；实例、时间策略和独立审计位于 `models/routing/common/cycle/`。两种求解方式按需加载、互不导入。`models/routing/service.py` 和 `cli/route.py` 在旧静态任务创建之前分流至新入口；`cli/multibatch.py` 负责新输出。费用核心位于 `models/cost/multibatch.py`，CLI 为 `cli/multibatch_cost.py`。M1–M4 由各自编号目录接入新版单批次；历史 M1–M4 只可显式使用 --baseline。

**2026-09-29 主线清理更新**

默认入口现为 `config/cases/single_batch_demo.json`，采用同一框架 B=1。历史单日版已隔离到 `models/routing/baselines/`，仅能显式启用。上文 253 项为清理前历史回归计数，清理后验收见 [清理记录](repository_cleanup_20260929.md)。旧 M5 中转、旧 M6–M8 与旧经济对比不再从正式命令调用。新版 M5 是本页的多批次模型。

编号修正说明：以前保存的多批次结果可能仍标为 M3；它们来自当时的源码快照。新运行标为 M5，旧结果可用显式迁移工具重标并重新审计，见 [布局与迁移](model_layout.md)。
