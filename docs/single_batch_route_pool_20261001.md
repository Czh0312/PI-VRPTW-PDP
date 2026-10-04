# 单批次 M1/M2：列生成路线池可行解优化

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Mode: run; user-authorized algorithm implementation and frozen comparisons
- Verification Status: EXECUTED; independent tiny-model and departure/vehicle oracles
- Scope: B=1 M1/M2; primal route pool plus certified root bound plus complete MILP

## 问题与实现

上一版根节点列生成改善了部分实例的下界，但把根节点时间从完整 MILP 预算中拿走后，
可行解质量不稳定。这一轮增加 `single_batch_route_pool.py`，直接利用已经生成的路线。

流程为：根节点列生成 → 路线池整数选路及实体车辆排程 → 独立审计 → 完整联合 MILP。
客户分仓、一级配送路线、二级路线和每辆车的每趟发车/回仓时刻，在路线池整数模型中一起选择。
模型保留实际一级到货、装车准备、等待、配送截止、回仓卸货、趟间周转、终端周转，
以及一级和二级的车辆数、重量/体积容量。同一客户多订单共享分仓，但订单仍是独立任务。

为了缩小这一步的整数模型，默认每辆车只搜索至多两趟（同时不超过政策原上限）。
这是可行初解搜索的限制；完整 MILP 使用政策原来的全部趟数。测试中特意设置需要三趟的实例，
验证路线池未找到解时，后续完整模型仍能使用第三趟并证明最优。

默认编号入口继续使用既有求解流程。新阶段通过 `solve_with_route_bound(..., pool_time=3)` 显式启用。
`root_time` 限制列生成阶段，`pool_time` 限制额外的路线池阶段；两者与完整 MILP 共同消耗
`time_limit` 的总墙钟预算，包括建模和审计时间。若路线池没有找到严格更好的可行解，保留原初解。

## 固定路线时间的精确压缩

纯配送固定路线从发车时刻 d 出发，每一客户的最早服务时刻递推为
`max(上一服务完成 + 原始行驶时间, 窗口下界)`。因此，在固定基准发车 r 之后，
最早回仓卸货完成时刻严格等于

    F(d) = max(d + L, F(r)),  d >= r

其中 L 是所有原始行驶、服务及回仓卸货时长之和。最新允许发车 U 则取每一客户窗口上界
减去到该客户的无等待前缀时长，以及终端期限减去 L 的最小值。
基准路线本身先按原始时间窗重放，确保所有等待项满足各个上界。

每个车辆趟次只需两个整数时刻 d/e，选择路线 c 时要求 `d <= U_c`、
`e >= d + L_c`、`e >= F_c(r)`。同时要求装车准备在实际到货和前趟周转之后才开始。
e 可以比最早回仓更晚；提取时重放最早回仓得到更早的完成时刻，仍满足所有后续车辆约束。
这样不用给每一个“路线 × 车辆 × 趟次”组合复制全部客户服务时刻变量。
推导不使用三角不等式，也不要求行驶/服务时长严格为正。

## 证明边界

路线池只含列生成已经发现的路线，且可行初解搜索采用较小的趟数限制。
路线池最优值、`ObjBound`、不可行状态及整数最优状态均不得转成原问题的证明。
模块仅返回独立审计通过且严格改善原初解的方案；该方案的 `proven_optimal` 和
`proven_infeasible` 都为 False，`best_bound` 为 None。

全局下界仍来自原来的保守定价修正证书，整数最优性仍由完整联合 MILP 证明。
根节点与路线池耗尽总预算时，保留审计通过的最好方案，并清除旧证明和旧下界。
M3/M4 明确拒绝此纯配送路线池，送取使用间隔和回收周期需要另外的列结构。

## 冻结对照与保留记录

结果根目录：`output/experiments/single_batch_pool_20261001/`。
输入和共同初解来自上一轮冻结的 20/30 客户 M2 场景；所有方法使用相同输入、初解、种子、
总预算，单线程、顺序独立进程，按种子反转方法执行次序。没有失败重跑。
输入、源码、参数、日志、结果、独立审计及两周期重放均保留。

- `short_budget/`：先整数选路再独立排程的探索版本。20 客户两种子各拒绝 12 个无法排程的组合，
  均未改善初解；30 客户的收益也不稳定。保留全部 8 次记录。
- `physical_pilot/`：把排程放入路线池整数模型后的单种子试验。20 客户、总预算 30 秒，
  距离 661.1818 → 535.3507，Gap 24.8249% → 7.1554%。这不是正式多种子结论。
- `final_short/`：最终实现，20 客户、种子 42/43、总预算 30 秒，列生成最多 10 秒、路线池最多 3 秒。
- `final_long/`：最终实现，30 客户、种子 42/43、总预算 60 秒，列生成最多 30 秒、路线池最多 5 秒。
- `full_milp_short/`、`full_milp_long/`：同输入、初解、种子、预算和 M1/M2 执行源码的完整联合 MILP 补充基线。
  此基线在另一个冻结批次顺序执行，没有和列生成混合方法交错，结果需结合正式成对对照理解。

## 正式成对结果：上一版列生成与新增路线池阶段

| 客户数 / 总预算 / 种子 | 上一版距离 | 新版距离 | 上一版 Gap | 新版 Gap | 两者下界 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 20 / 30 秒 / 42 | 661.1818 | 543.0707 | 24.8249% | 8.4752% | 497.0444 |
| 20 / 30 秒 / 43 | 658.6001 | 543.0707 | 24.5302% | 8.4752% | 497.0444 |
| 30 / 60 秒 / 42 | 863.3645 | 680.4876 | 34.7744% | 17.2454% | 563.1347 |
| 30 / 60 秒 / 43 | 839.7334 | 680.7361 | 32.9389% | 17.2756% | 563.1347 |

4/4 组同时改善距离与 Gap，距离降低 17.54%～21.18%；8/8 个结果通过独立审计与两周期重放。
本次 30 客户的根节点在 30 秒内没有完全收敛，因此只报告实际取得的保守下界 563.1347，
不能套用上一轮较长根节点诊断中的 609.47。定时定价和有限启发式插入也会使不同运行的
路线池不同；因此，535.35 的探索结果不能替换正式结果 543.07。

新版路线池阶段实际耗时：20 客户为 0.627 / 0.812 秒，30 客户为 2.891 / 3.738 秒，
均在所分配上限内。该阶段的路线池模型均求到内部最优，但原问题最优证明仍为 0→0。
30 客户 seed 42 的路线池初解为 680.7361，后续完整 MILP 又改善到 680.4876。
其他三个正式新版结果保留了路线池找到的最好可行解。

## 补充结果：完整联合 MILP 基线

| 客户数 / 总预算 / 种子 | 完整 MILP 距离 | 新版距离 | MILP 下界 | 新版下界 | MILP Gap | 新版 Gap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 20 / 30 秒 / 42 | 547.8897 | 543.0707 | 458.0619 | 497.0444 | 16.3952% | 8.4752% |
| 20 / 30 秒 / 43 | 650.9297 | 543.0707 | 457.1305 | 497.0444 | 29.7727% | 8.4752% |
| 30 / 60 秒 / 42 | 797.2334 | 680.4876 | 515.7909 | 563.1347 | 35.3024% | 17.2454% |
| 30 / 60 秒 / 43 | 725.5746 | 680.7361 | 511.1585 | 563.1347 | 29.5512% | 17.2756% |

4/4 个补充比较同时改善距离与 Gap，距离降低 0.88%～16.57%。正式与补充共 12 个结果，
全部通过独立审计与两周期重放，原问题最优证明数为 0。
这些是两个规模、每个规模两个种子的有限样本；补充基线也没有与混合流程交错执行。
因此结论是本次有限预算内的可行解和 Gap 得到改善，尚无证据宣称全局最优证明耗时缩短。

所有 M1/M2 求解、定价、路线池、审计及实验运行源码的 SHA256 在各正式批次一致。
源码全集快照有三个未在本试验中执行的多批次/ALNS 模块差异，记录在 `summary.json`：
`multibatch_repair.py`、ALNS `solver.py`、`multibatch_e1_polish.py`。未修改或回退这些工作区变更。

新阶段保留为显式实验选项。后续扩大实例/种子范围，以及检验是否能缩短真正的最优性证明时间，
才能判断是否适合接入默认精确求解入口。

## 验证

- 44 项新路线池测试及 88 项既有列生成测试全部通过（132 项）。
- 独立穷举 M1/M2 微型原模型；枚举每一个整数发车时刻核对固定路线时间压缩；
  独立枚举车辆分配和趟序核对实体排程，包括准备/周转、等待、重复地址、零时长及趟数不足。
- 验证两趟初解搜索的限制不进入完整 MILP，三趟反例仍可被完整求解器证明最优；
  路线池不可行、超时、内部最优及原初解证明均不被导入全局证明。
- 全库 `1102 passed, 14 warnings in 48.68s`；警告来自既有 Matplotlib/Pyparsing 弃用提示。
- 计时实验顺序执行，没有和测试或其他本聊天的求解探针重叠。
- 验证前后参与此次修改和核心求解的源码 SHA256 无变化。

原始审计、汇总、参数和验证记录见结果目录的 `summary.json`、`validation.json`、
`validation_before_full_tests.json` 及 `full_tests.log`。

## 复现

```python
from models.routing.solvers.exact.single_batch_columns import solve_with_route_bound

result = solve_with_route_bound(problem, time_limit=60, root_time=30,
    pool_time=5, pricing_method='mip', initial_result=audited_start)
```

```sh
.venv/bin/python experiments/runs/run_single_batch_columns.py \
  --frozen-runs output/experiments/single_batch_site_flow_20261001/final/zhou_Set2_100-5-1b_subset20_M2 \
  --methods columns columns_pool --seeds 42 43 \
  --total-time 30 --root-time 10 --pool-time 3 --pricing-method mip \
  --output output/experiments/single_batch_pool_new_short

.venv/bin/python experiments/runs/run_single_batch_columns.py \
  --frozen-runs output/experiments/single_batch_site_flow_20261001/final/zhou_Set2_100-5-1b_subset30_M2 \
  --methods columns columns_pool --seeds 42 43 \
  --total-time 60 --root-time 30 --pool-time 5 --pricing-method mip \
  --output output/experiments/single_batch_pool_new_long

.venv/bin/python -m pytest tests/routing/test_single_batch_route_pool.py tests/routing/test_single_batch_columns.py -q
```
