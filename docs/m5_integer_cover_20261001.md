# M5 第十四步：完整路线域的整数覆盖下界

日期：2026-10-01。

本轮将上一轮完整二层路线覆盖 LP 的变量改为二元变量，计算更强的整数松弛下界。50 顾客完整可行上界保持 **2261.8315**，组合下界从 **2195.3268 提高到 2209.4973**，gap 从 **2.9403% 降至 2.3138%**。三个种子得到相同下界，在原 60 秒总预算内用时 25.35–27.93 秒。

20 顾客的 gap 从 1.0605% 降至 0.7531%。30 顾客的原覆盖 LP 已有整数最优解，本轮没有实质提高其数学下界，仅减少保守数值修正造成的损失。

这些结果证明的是**完整二层整数覆盖松弛**的最优值。实际一级供应、跨趟使用时长、物理车辆串行与清洗库存连接仍被放松，不能将其路线选择当作完整 M5 可行日程，也不能据此声明完整 M5 最优。

## 1. 使用入口

```python
from models.routing.variants.model_5.exact import route_cover_certificate

certificate = route_cover_certificate(
    problem,
    initial_result=audited_result,
    time_limit=60,
    e1_time_limit=10,
    pricing_backend='hybrid',
    integer_cover_time_limit=15,
    diagnostics_dir='output/experiments/my_m5_integer_certificate',
)
```

新实现位于 [`multibatch_route_integer_cover.py`](../models/routing/solvers/exact/multibatch_route_integer_cover.py)，由 [`multibatch_route_enumeration.py`](../models/routing/solvers/exact/multibatch_route_enumeration.py) 在完整路线域 LP 完成后调用。公开入口仍为 `route_cover_certificate`。

`integer_cover_time_limit` 默认是 **0**，保持上一轮行为；开启时要求 `pricing_backend='hybrid'`。每个仓的整数阶段最多占用指定上限与该仓原剩余预算的较小值。完整枚举、LP、整数转换、整数求解、一级证书和诊断导出共用原总预算。

如果枚举未完成，继续原完整 MILP 定价，**不**对部分路线池求整数下界。整数阶段建模或求解达到预算时，至少保留已经有效的 LP 对偶证书，并在有有限求解器下界时取更强的界。受限候选池整数最优值不具有本轮的全域下界资格。

## 2. 为什么整数覆盖仍是有效下界

完整 M5 的每条二层趟次可投影到上一轮保留的松弛任务路径。对于相同任务集合，可以替换成枚举域中成本最低的可行顺序；这个替换不保证真实跨趟日程，因此只用于构造松弛覆盖。

完整方案中每项配送/回收任务恰好服务一次，所有趟次对应整数路线选择，并满足有限总趟数上限。每条路线非空，覆盖至少一项恰好覆盖一次的任务，所以整数路线使用量不可能超过 1，二元变量不会排除完整方案的覆盖投影。

因此，所有完整可行方案的二层成本均不低于该仓完整整数覆盖的最优值。各仓二层界与独立一级成本界相加，仍是完整 M5 的下界。这不同于上一轮非完备候选池优化：本轮只接受已经穷尽的完整任务集合路线域。

提供已审计完整初解时，按原二层趟次的任务集合映射到最低成本列，作为整数松弛的热启动。该映射必须恰好覆盖所有任务、符合有限趟数，并且替代成本不高于原趟次成本；原方案本身不被修改。

## 3. 限时与证明字段

整数求解使用完整模型的 `ObjBound`，按 `1e-7 * max(1, abs(ObjBound))` 扣除数值余量，再与已有 LP 界取最大值。求解器的当前整数解目标只能作为该松弛的目标，不能作为完整 M5 上界，更不能在尚未最优时充当下界。

每仓新增 `lp_bound` 与 `integer_cover`。后者保存完整域状态、整数求解器原始下界、余量、松弛目标、选中列、初解投影和用时。原 LP 对偶迭代及正值 LP 列继续单独保存。

顶层 `INTEGER_RELAXATION_OPTIMAL` 表示所有非空仓的完整整数覆盖已完成；`PARTIAL_INTEGER_RELAXATION_BOUND` 表示整数阶段或完整枚举未完成。`integer_cover_complete` 明确记录这个状态。完整 M5 的 `proven_optimal`、`proven_infeasible` 仍为 `False`。

诊断保留三类文件：完整路线域 `*_best_pricing.json.gz`、连续主问题 `*_best_pricing_master.mps.gz`、完整二元主问题 `*_best_pricing_integer_master.mps.gz`。二元主问题沿用全部原列、成本、精确覆盖行和有限趟数行，只有变量域改变。

## 4. 同总预算、多种子对照

两组共用同一冻结输入和经过完整审计的上界；都现场完整枚举路线并启用同样的一级证书，没有加载上一轮的路线域或主问题。`lp` 只计算连续覆盖，`integer` 在原 60 秒内再计算完整整数覆盖，每仓上限 15 秒。种子为 42、43、44，共 18 次。

| 顾客数 | 冻结可行上界 | 连续组合下界 | 整数组合下界 | 连续 gap | 整数 gap |
|---:|---:|---:|---:|---:|---:|
| 20 | 1437.4738 | 1422.2295 | **1426.6488** | 1.0605% | **0.7531%** |
| 30 | 1654.8221 | 1623.9767 | 1623.9782 | 1.8640% | 1.8639% |
| 50 | 2261.8315 | 2195.3268 | **2209.4973** | 2.9403% | **2.3138%** |

| 50 顾客种子 | 连续组总用时 | 整数组总用时 | 整数覆盖状态 |
|---:|---:|---:|---|
| 42 | 21.79 秒 | 25.35 秒 | 全部仓最优 |
| 43 | 20.29 秒 | 27.93 秒 | 全部仓最优 |
| 44 | 24.02 秒 | 27.84 秒 | 全部仓最优 |

这轮用更多计算时间取得更强下界，未声称降低求解时间。数值结果仅描述本组输入，见 [正式多种子汇总](../output/experiments/m5_integer_cover_20261001/final_60s/summary.json)。

50 顾客的实质收益来自 S1：连续覆盖原始目标为 **437.5473**，完整整数覆盖最优值为 **451.7143**，提高 14.1671。其他三个仓的连续与整数最优目标相同。20 顾客在 S3 增加约 4.4184；30 顾客所有仓的原 LP 都存在整数最优解。

## 5. 测试与独立复核

新增 [18 项测试](../tests/routing/test_multibatch_integer_cover.py)，以六种有向非度量小例的全部可行任务排列，独立建立整数主问题，核对本轮成本并与完整 M5 已知最优值比较。一个三订单案例的覆盖 LP 最优值为 4.5，完整整数覆盖为 5，专门验证严格的整数下界提升。

其他测试覆盖完整初解投影与输入不变、部分枚举禁用整数证书、建模期限保留 LP 下界、求解器限时且没有整数解时使用原始下界、极小整数预算、参数校验、默认关闭，以及公开入口的连续/二元 MPS 分离导出。

相关回归 **470 项通过**，14 条警告来自原 Matplotlib/PyParsing 依赖；该总数包含共享目录中同期新增的一级后端测试。见 [测试日志](../output/experiments/m5_integer_cover_20261001/pytest.log) 与 [回归命令](../output/experiments/m5_integer_cover_20261001/run_regression.sh)。

另对 7 个已知精确小例运行 `lp`、`integer` 两组，共 14 次，所有证书均不超过已审计的完整整数最优值，见 [小例汇总](../output/experiments/m5_integer_cover_20261001/final_small_5s/summary.json)。对 30/50 顾客强制枚举状态上限为 1，共两次，确认不完整列域不会进入整数证书，见 [回退汇总](../output/experiments/m5_integer_cover_20261001/final_fallback_60s/summary.json)。

总计 34 次正式运行均冻结输入、源码、参数、已审计上界和日志。工具为 [`benchmark_multibatch_integer_cover.py`](../tools/audit/benchmark_multibatch_integer_cover.py)，执行脚本为 [run_formal.py](../output/experiments/m5_integer_cover_20261001/run_formal.py)。

[总复核脚本](../output/experiments/m5_integer_cover_20261001/validate.py) 独立审计上界、全部路线、上下界组合和 LP 对偶证书。[路线域复核器](../output/experiments/m5_integer_cover_20261001/validation_enumeration.py) 从头重算每个候选前缀的载荷和完整时间线，核对完整任务集合最低成本，并检查全部连续 LP 矩阵。[整数复核器](../output/experiments/m5_integer_cover_20261001/validation_integer.py) 对比连续与二元 MPS 的全部成本、覆盖矩阵和有限趟数行，确认只有变量域改变，重新求解二元主问题，并从其变量值独立重建任务覆盖和成本；另核对每条原完整趟次到最低成本列的投影。

最终复核记录见 [validation.json](../output/experiments/m5_integer_cover_20261001/validation.json)，本轮定向改动见 [changes.patch](../output/experiments/m5_integer_cover_20261001/changes.patch)。

最终复核通过全部 **34** 次正式运行，重读求解 **90** 份连续 LP、**45** 份完整整数覆盖模型和 **34** 份一级模型，核对连续 LP 的 **1,862,754** 条列和 **304** 份对偶证书。去重后的 **25** 个仓级完整路线域均与独立前缀重枚举一致；全部整数模型的覆盖矩阵与对应连续模型逐项一致。

正式实验之后，共享列生成代码又增加了默认关闭的分批定价和对偶修复选项。本轮完整枚举与整数求解实现及接入分支未变，正式结果按冻结源码复核；另对当前公开入口运行定向兼容回归，记录见 [补充测试日志](../output/experiments/m5_integer_cover_20261001/post_concurrent_api.log)。本轮改动补丁不包含同期定价功能。

## 6. 下一步

50 顾客仍有约 **52.33** 的可行上界与证书差额。所有仓的完整整数覆盖现已达到最优，继续求解同一个二层覆盖松弛不会再实质提高其界。后续需要将真实供应、回收截止、清洗或物理车辆串行连接加入更强的松弛，或继续改善完整可行方案。

当前完整方案的一级/二层成本分别为 1056.4382、1205.3933，对应下界为 1021.3394、1188.1581；两部分差额约为 **35.10** 和 **17.24**，另有极小的最终数值余量。下一轮优先检查一级清洗库存与二层回收截止的联合连接。
