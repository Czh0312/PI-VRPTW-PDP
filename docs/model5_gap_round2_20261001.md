# M5 GAP 第二轮：五仓一级枚举与双向对换

日期：2026-10-01。继续以每个大规模算例的原 M5 认证 GAP ≤5% 为目标。

本轮两组 100 顾客 B3 开发配置均改善：完整箱池 **30.65%→17.50%**，紧箱池 **39.24%→15.47%**。仍未达到 5%。此前两组 50 顾客结果保持 2.52% 和 2.31%；四组已观察配置中仍为 2/4 达标，不能推广到所有大规模实例。

## Material Passport

- 实验 ID：`model5_gap_round2_20261001`。
- 执行模式：连续执行；不新增研究范围，不需要逐阶段确认。
- 数据：上一轮相同冻结网络、订单、批次、有限车队与箱池，仅两组已观察的 100 顾客 B3 配置。
- 证据：[实验注册](../output/experiments/model5_gap_round2_20261001/plan.json)、[源码与输入清单](../output/experiments/model5_gap_round2_20261001/manifest.json)、[最终独立验证](../output/experiments/model5_gap_round2_20261001/validation.json)。
- 状态：本轮实验与审计完成；全量 5% 目标未完成。

## 最终结果

| 100 顾客 / 箱池 | 上轮可行 UB | 本轮可行 UB | 上轮有效 LB | 本轮有效 LB | 上轮 GAP | 本轮认证 GAP |
|---|---:|---:|---:|---:|---:|---:|
| 完整池 156 | 2300.631170 | 2269.690002 | 1595.546953 | 1872.479464 | 30.65% | **17.50%** |
| 紧池 78 | 2619.772128 | 2377.701351 | 1591.865289 | 2009.821763 | 39.24% | **15.47%** |

两个可行解分别比上轮降低约 1.34% 和 9.24%。GAP 的大幅下降同时来自下界加强，不能解释为可行路线成本下降了 17% 或 24%。GAP 按 `100*(UB-LB)/abs(UB)` 计算，不用受限子问题 bound 替代原 M5 下界。

最终方案：[完整池](../output/experiments/model5_gap_round2_20261001/pair_exchange_30s/100_full.json)、[紧池](../output/experiments/model5_gap_round2_20261001/exchanges_180s/100_tight.json)。两个方案都通过完整周期和周期重放审计。

## 实际实现

1. **可选五仓一级完整路线枚举。** 两组 100 顾客有五个活跃仓，之前因四仓的性能门槛进入弧模型。实际容量和日程筛选后，完整一级路线仅 11706 条。完整枚举模型分别在约 3 秒内证明一级松弛最优，安全下界为 935.197567 与 1072.539880。模型仍松弛二级联动，这不是原 M5 的最优证明。
2. **可选双向任务对换。** 原候选池支持单向转移，但满载趟次往往不能先加入任务再移出。新增互补对换，同时生成两趟的替代路线，涵盖单任务、完整订单 D/P，以及两个同类任务。只在同仓趟次之间生成；保留原路线，仍由精确覆盖、负载、供货、回收、清洗、物理车与时刻约束选出完整可行方案。
3. **公开入口与保留默认行为。** `exchanges=False`、`e1_backend='auto'` 的默认行为保留；自动一级选择仍以四仓为门槛。五仓枚举由 `e1_backend='patterns'` 显式启用。受限可行解主问题要求至多五仓且 E1 周转为零；正周转须使用弧模型。一级下界枚举最多五仓，允许松弛物理车周转，但不输出完整模型最优性。

涉及 [`multibatch_route_pool.py`](../models/routing/solvers/exact/multibatch_route_pool.py)、[`multibatch_e1_inventory.py`](../models/routing/solvers/exact/multibatch_e1_inventory.py)和 [`multibatch_route_cg.py`](../models/routing/solvers/exact/multibatch_route_cg.py)。公开入口的示例：

```python
from models.routing.variants.model_5.exact import reoptimize_route_pool, route_cover_certificate

better = reoptimize_route_pool(
    problem, audited_alns_result, time_limit=120,
    exchanges=True, max_columns=256, e1_backend='patterns', seed=42,
)
certificate = route_cover_certificate(
    problem, initial_result=better, time_limit=120,
    e1_time_limit=20, e1_backend='patterns', seed=42,
)
```

这些是依赖 Gurobi 的混合后处理选项。基础 ALNS 不依赖此求解器，本轮没有修改 ALNS 搜索核心。

## 下界共享与未改善试验

完整池、紧池的二级松弛均不保留箱池与清洗复用约束。对每个仓，复核任务窗口、订单服务与使用时间、配送批次、必要供货时刻、重量体积、车队总趟数、每条有向弧的原距离与整数时间、周期末条件，得到相同的松弛指纹，才允许共享二级下界。

采用逐仓最大有效下界，合计约 937.282084；再分别加入箱池各自的一级下界，并扣除 `1e-7*max(1,raw)` 的全局余量。**一级下界没有跨箱池共享。** 每项二级下界的来源文件与完整精度记录在最终验证 JSON。

另对未收敛的 S1、S4 各集中定价 150 秒，使用两份已审计方案的单趟路线作种子列；两次都限时退出且该方法的有效新下界仍为 0，未替代已有距离下界。该无收益试验完整保留，未采用受限主问题目标或定价 incumbent 作全局下界。

完整池的最终一级实际距离约 993.69，二级约 1276.00；紧池约 1093.21 与 1284.49。剩余证明缺口主要在二级松弛，尤其 S1/S4 的未完成定价。下一轮需要加强二级下界和多任务重组，仅延长相同定价方法尚无有效证据。

## 预算与中间阶段

| 阶段 | 额度 | 实际结果 |
|---|---|---|
| 五仓路线池 | 每组 120 秒 | 完整池约 10.16 秒，紧池约 13.34 秒 |
| 全仓认证 | 每组 120 秒，其中 E1 最多 20 秒 | 两组均完成部分认证；E1 松弛最优 |
| 单任务/完整订单互补对换 | 每组 180 秒 | 约 26.76 秒和 11.94 秒 |
| S1/S4 集中定价 | 每仓 150 秒，共享二级松弛 | 均无更强新下界 |
| 两个同类任务对换 | 每组 30 秒 | 约 27.11 秒和 6.31 秒；完整池再改善，紧池不变 |

本轮在之前构建的解之上增加预算。正式统一时限的用户偏好仍未收到答复；这不是同预算纯 ALNS 对照，也不是多种子稳定性验证。

早期五仓实验将自动性能门槛临时扩至五仓，仅在冻结实验源码中执行。验证后落地的公开 API 使用显式选项，默认门槛保持四仓。生产入口与实验使用相同完整一级模型和审计条件。

## 验证

相关回归 **100 passed**，见 [最终日志](../output/experiments/model5_gap_round2_20261001/pytest_all_final.log)。新增用例证明：满载路线单向转移无法生成的任务集合可以通过互补对换生成；完整方案改善并与小例全 M5 的已证最优值一致；五仓公开入口输出审计可行解和有效松弛下界；受限子问题不报告全 M5 最优；正 E1 周转和六仓枚举会被明确拦截。

独立验证重读方案、所有候选列、CG 对偶见证和 MPS。用独立的全排列路线 oracle 复核 11706 条一级路线，重求松弛 MPS，并固定完整方案在一级模型中的投影，检查原一级成本保持。列逐项重放原距离、载荷、窗口、准备、使用时间、回仓、周转与截止条件。最终 GAP 由独立重算的下界与已审计 UB 得出。

本轮独立验证通过 8 次完整周期审计、33075 个候选/定价列重放、699 个距离对偶见证复核，以及两份一级松弛 MPS 的最优值重求和完整方案投影。验证脚本已再次执行通过，见 [复核日志](../output/experiments/model5_gap_round2_20261001/validation_reproduce.log)。

所有实验使用冻结源码。冻结的 12 个 ALNS 核心文件与上一轮一致；共享工作区后来另有 `neighborhoods.py` 的单批缓存参数变化，本轮未覆盖它。工作区差异及冻结哈希均写入验证记录。

复核：

```bash
.venv/bin/python -m pytest tests/routing/test_multibatch_joint_exchanges.py \
  tests/routing/test_multibatch_e1_inventory.py tests/routing/test_multibatch_route_pool.py \
  tests/routing/test_multibatch_route_cg.py tests/routing/test_solver_isolation.py -q
.venv/bin/python output/experiments/model5_gap_round2_20261001/validate.py
```

## 方法解释检查

**11/11 已检查**：逐规模/箱池报告避免 Simpson 聚合误读；不从两组实例推广全体避免生态谬误；已观察配置属于选择样本，记录 Berkson 偏差；无条件回归，collider 不适用；明示仍为 2/4 达标避免基率忽视；最好解起点和新增预算不能视为同预算算法效应，控制均值回归误读；保留无收益定价和回归初检日志避免幸存者偏差；所有阶段均记录，不挑选有利结果；后续对换是探索而非确认性试验，记录分叉路径；不把混合阶段的收益归因于单个 ALNS 改动；不作因果或反向因果推断。无显著性、置信区间或稳定性提升声明。
