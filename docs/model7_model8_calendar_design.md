# 模型7与模型8：逐批分界及批次长度优化

2026-10-05 实现。两模型都是静态全信息、固定分仓、两级配送与容器循环的成本模型。新增编号重新用于本文定义，不恢复已归档的旧 M7/M8。

2026-10-15 批次业务定义修正：M6–M8 每个计入 K 的批次都必须有正的配送箱量和回总仓箱量，不能以空日历段延后装货。精确模型、ALNS 和独立审计共同执行此规则。下面的批次是实际送取作业窗口；此前允许配送关闭或整段空闲的结果属于旧允许域，不能直接作为修正后模型的实验结论。

| 模型 | 周期批次数 K | 各批长度 | 批内分界 |
| --- | --- | --- | --- |
| M6 `uniform_cycle` | 正整数，由周期和最短阶段约束定界 | H/K | 共同相对时长 d |
| M7 `uniform_independent_cutoffs` | 正整数，由周期和最短阶段约束定界 | H/K | 各批独立 d_b |
| M8 `flexible_cycle` | 正整数，由周期和最短阶段约束定界 | 各批独立 L_b，总和 H | 各批独立 d_b |

## 数学范围

给定原周期 [t0,t1]，H=t1−t0，最短阶段时长 m。在每个固定 K 的子问题内，M7 使用 s_b=t0+(b−1)H/K、e_b=t0+bH/K；每个 c_b 独立满足 s_b+m≤c_b≤e_b−m。

M8 的批次边界为整数刻度变量 x_0,…,x_K，约束 x_0=t0、x_K=t1、x_{b−1}+m≤c_b≤x_b−m。令 s_b=x_{b−1}、e_b=x_b、L_b=x_b−x_{b−1}，因此批次覆盖完整周期，连续且不重叠，每批至少 2m。无额外比例相等、长度候选网格或需求排序限制。2026-10-07起，K默认为全部 `1≤K≤floor(H/(2m))` 的正整数；M6/M7在不能整除原刻度时自动细分该K的时钟，保持原物理时长和严格等长；M8保留原时钟。

订单分配 y_ib、配送启用 z_b、箱池 N、两级路线、车辆和实际作业时刻继续联合优化。各模型使用同一成本评价，包括箱池、距离、车辆、趟次、占用工时和固定周转费用。成本参数的来源状态保持不变。

关键时间衔接全部使用选定的实际日历：

- y_ib=1 ⇒ s_b≥release_i；货物在领箱装货前已经可用。
- 有配送的一级车辆在 s_b 装货，完成装货后发车，并在 c_b 前完成向前置仓供货。
- 二级卸回前置仓的时刻≤c_b，才进入本轮一级截止全取量；晚于截点的空箱进入后续轮次。
- 一级回运从 c_b 后执行，并在 e_b 前卸回总仓；各车满足连续占用与周转间隔。
- 在 s_b 领箱时，只计实际一级卸箱完成加清洗时长≤s_b的回流箱量。累计领箱量不能超过初始箱池加已清洗释放量。
- 每个批次必须配送至少一笔订单，并回运至少一个空箱；送出和取回的数量不要求相等，回收可以来自之前批次。
- 末轮回运及清洗必须在原 t1 内完成；不增加隐藏回收轮次。中间批次只要求本轮一级回运在批末卸回总仓，不要求本批全部配送箱当批收齐或当批洗完。

顾客绝对配送窗、回收窗、货物释放、最短使用时长、物理网络、资源和费用均不随日历平移。二级车辆仍可在第二阶段配送、取箱及返回；批内截点是本轮一级回运资格边界。不是强制所有顾客送取均发生在其配送批次内。

截点按二级车辆在前置仓完成卸箱的时刻分类；恰好在截点完成的空箱属于本轮，晚于截点的归入后续轮次，即使一级车尚未到达也不扩大本轮资格。本轮符合资格的箱子必须全取，一级车辆须在批末前回总仓卸完。取回箱子只有在实际卸回总仓并完成清洗后才能用于后续领箱。

固定 K 的 ALNS 初始分配以订单允许批次为边构造覆盖所有批次的匹配；订单移动不清空原批次。匹配只证明非空配送分配是否存在，不证明路线或回收可行。变量边界 M8 在每次日历重建后重新修复覆盖，不能把某个固定日历的匹配失败当作整个不等长允许域不可行。K 大于订单数时不可能有 K 个非空配送批；显式候选域仍保留，按候选分别报告结果。

## 精确求解

分别通过 `model_7/exact/`、`model_8/exact/` 入口运行。按配置枚举 K，所有 K 共享一次调用的总时间预算。

M7 对每个 K 求解具有 K 个独立截点变量的完整联合 MILP。M8 对每个 K 求解含 K−1 个内部边界变量和 K 个截点变量的完整联合 MILP；批次起止直接参与释放、路线时间和箱池供给约束。M8 不枚举所有长度组合，也不把 ALNS 找到的日历当成精确允许域。

两模型分别拥有 `models/routing/model_7/exact/joint_model.py` 和 `models/routing/model_8/exact/joint_model.py`，独立维护路线、装载和成本建模。各自的 `joint_solver.py` 负责固定 K 子问题；M8 提取边界后重建固定日历，再投影物理方案并计价。模型身份、决策域及最终证明范围由各编号入口和各自的 `batch_search.py` 管理，不再导入 M6 的求解实现。

所有配置 K 都证明最优或不可行时，可声明整个模型最优。另一个充分条件是：所有未证不可行的 K 均有有限的有效下界，其最小值不低于已独立审计的可行成本；此时尚未结案的 K 也不可能改善当前方案。输出 `proof_aggregation` 区分这两种全局证书，不把被界排除的 K 标成单独最优。未执行的 K、缺失下界及审计失败会阻止按界证明全局最优；未闭合时保留可行上界和各 K 下界。结论只覆盖给定 K 集合和整数时间精度。

## ALNS

M7 通过自己 `alns/joint_solver.py` 中的独立分界成本搜索实现，逐批执行分界移动、回收/清洗事件对齐，并与订单重新分批、路线破坏修复、车辆和访问顺序操作共同搜索；不会同步移动所有截点。

M8 的 `alns/search.py` 在上述操作基础上增加：

- `shift_boundary`：移动相邻批次之间的边界，保持总周期与最短阶段。
- `align_boundary`：把边界对齐货物释放、可达配送窗边界、回运清洗完成或车辆周转事件。
- `redistribute_lengths`：重新分配多个批次长度，并修复分配和路线。

每次改变日历，都重新计算合法订单批次，修复受影响的分配和路线，再通过物理传播及成本审计评价。缓存按完整边界、订单分配、截点和路线区分。事件种子允许从均匀日历不可行的实例进入非均匀可行区域。算子权重按搜索收益更新，采用模拟退火接受和最优解重启。

ALNS 不导入 Gurobi 或精确后端；精确法也不导入 ALNS。两模型都可接收同模型、同允许域的已保存可行解，重新独立审计后保留其成本上界，不继承最优证明。总时间/迭代预算在 K 间共享；初始化和一次不可分割的解码/审计可能产生少量时间超出，不额外给每个 K 完整预算。有限时间 ALNS 不保证 M8 成本低于 M7。

2026-10-05 测评后补充：M7/M8 初始化加入最早可能末批回收时刻对应的截点种子，以覆盖周期末端的窄可行区域；M8 对边界事件去重，K=1 时采用固定日历搜索及截点精修（当前代码位于 M8 自己的 `alns/joint_solver.py`，不调用 M7）；M7/M8 精确法在全局上下界闭合时正确汇总最优证明。测评结论、仍存限制及冻结证据见 [模型7/8测评报告](../output/inspections/model78_review_20261005/model78_review_20261005.md)。

## 输入和运行

保留 schema_version=2、multi_batch_cutoff_v1 及 `joint_batch_options`；必须提供显式 release_time、allowed_batch_ids 和 economic_config。重新划分周期要求每笔订单允许全部原日历批次；不能静默丢掉已有的批次限制。省略批数配置或设为 `all_positive_integers` 时，K上界仅由周期和阶段最短时长确定。m 默认 1 分钟，仍为可配置的场景假设。显式K列表保留用于历史复现或诊断；旧输入可用CLI的 `--all-batch-counts` 覆盖。

```json
{
  "model": 8,
  "joint_batch_options": {
    "schedule_mode": "flexible_cycle",
    "batch_count_candidates": "all_positive_integers",
    "min_phase_minutes": 1,
    "pool_min": 1,
    "pool_max": 131
  }
}
```

模型7将 model 设为7、schedule_mode设为 `uniform_independent_cutoffs`。`pool_max`不能超过当前实例需求总箱次。

```sh
.venv/bin/python main.py route --case config/cases/model7_demo.json --solver exact --exact-time 30
.venv/bin/python main.py route --case config/cases/model7_demo.json --solver alns --time 3 --iterations 3000
.venv/bin/python main.py route --case config/cases/model8_demo.json --solver exact --exact-time 30
.venv/bin/python main.py route --case config/cases/model8_demo.json --solver alns --time 3 --iterations 3000

# 使用现有M6均匀数据，保持原物理输入和K范围：只切换编号和日历模式
.venv/bin/python main.py route --case data/model6/uniform_v2_20261005/lade_0823_10aoi/spread_medium/m6_case.json --model 8 --solver alns --time 10
```

M7/M8 不接受 M6 历史候选日程的 `--fixed-cutoffs`、`--optimize-cutoffs`、`--candidate-slots` 和 `--uniform-cycle` 开关；新编号本身指定日历规则。`--batch-count-candidates`、`--min-phase-minutes`、固定箱池和同模型初始解开关可用。旧候选模式中的固定分配、手工截点界不能默默迁移，会拒绝不支持的配置。

示例7沿用高池费两订单机制例；示例8另设第二单在70分钟备好、配送窗75–90分钟。130分钟分两等长批的第二次领箱是65分钟，无法接纳第二单；M8可将边界移到70分钟或以后。此例仅演示可行域差异，不用于推断现实收益。

## 输出和独立验证

输出保存模型编号、schedule_mode、calendar_config、cycle_batch_count、batch_boundaries、batch_durations_ticks、batch_cutoffs、cutoff_offsets_ticks、cutoff_ratios 和 count_search。周期划分数、实际有配送的批次数、实际空箱回运轮数分开报告。

修正后的有效方案中，这三个数量均等于 K。actual_recovery_round_count 按有正回收量的 batch_id 去重；同一轮取回两个前置仓的箱子仍只计一轮。旧 M7/M8 字段按“前置仓×批次”计数的问题已修正，原始历史文件不改写。独立审计用 EMPTY_BATCH_DELIVERY、EMPTY_BATCH_RECOVERY 拒绝无实际作业的批次，历史方案须重新审计后才能作为新允许域的初解。

`models/common/cycle/calendar.py` 依据原输入允许域重建选定日历，再检查截点、车辆、订单、库存、清洗、成本和两个连续周期。结果文件自行修改的日历范围、长度、分界比例和缓存成本均不能代替独立验证。

回归测试入口：

```sh
.venv/bin/python -m pytest tests/routing/test_model78_calendar.py -q
```

测试包括：M7独立分界严格改善例；M8移动边界恢复释放可行性；极小整数时间网格的所有固定日历/截点穷举，与完整MILP最优值核对；篡改审计；跨周期清洗和复用；K未决时不误报最优；ALNS保留可行起点；求解器导入隔离；命令行及冻结输入。较大算例的限时表现另行报告，不能从机制测试推断收敛或大规模性能。
