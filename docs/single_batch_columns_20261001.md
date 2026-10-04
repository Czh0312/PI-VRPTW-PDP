# 单批次 M1/M2 根节点列生成原型

后续已实现并验证从列生成路线池构造实体车辆可行初解，参见
[路线池排程优化记录](single_batch_route_pool_20261001.md)。本文件保留首轮下界原型的历史对照。

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Mode: run; user-authorized algorithm prototype and code experiments
- Verification Status: EXECUTED; independent pricing/original-model oracles and frozen benchmarks
- Scope: single-batch M1/M2 delivery only; M3/M4 and full branch-and-price are not implemented here

## 实现与适用范围

`single_batch_columns.py` 提供 `root_route_bound` 和显式的 `solve_with_route_bound`。
默认编号模型入口不调用列生成。实验混合求解把根节点计算和完整 MILP 放在同一个总墙钟预算中。

一级完整枚举有序仓点子集，保留有限一级车辆、仓点唯一供货和货物重量/体积容量。
主问题将同一顾客的订单绑到共同仓点，并通过顾客到一级路线的连续货量分配协调一级容量。
一级列使用固定发车时刻的最早日程。纯配送下提前到货不会破坏可行性，且没有回收等待。
枚举截止或达到上限时，不能把不完整一级列池用来给出新的下界或不可行证明。

二级列是按订单身份、不是物理地址身份的初等单趟配送路线；不同订单可以重复访问同一地址。
保留两维出发载重、整数时间窗、配送截止、回仓卸货和终端周转期限。
两种定价器计算同一个路线集合：

- `single_batch_route_pricing.py`：带时间/费用 Pareto 支配的标签算法，有限标签/时限下使用未展开前沿的保守下界。
- `single_batch_delivery_pricer.py`：可复用的单趟 MILP；容量只需限制出发配送总量，因纯配送载重单调下降。
  使用 `IntFeasTol=1e-9`，独立重放返回路线并核对原始约化费用。

**放松了实际一级到货与二级发车的同步、二级实体车辆的逐趟排程。**
只保留每仓总趟数的必要上限。因此这是一套可证明有效的下界模型，不能把它的整数路线池解
或 LP 收敛状态称为原模型最优解。M3/M4 明确拒绝，不能遗漏送取使用间隔后直接套用。
M1 中同一顾客的输入订单若指定不同仓点，该原型明确拒绝；生产 M1 的行为不改变。

## 下界与证明边界

限制主问题的目标值 `restricted_objective` 可能高于原问题最优值，不能直接拿来剪枝。
在一轮 LP 最优对偶下，对每仓完整定价问题取得下界 δ_s（可以是未完成定价的有效下界），
每仓路线总数至多 K_s，则使用

    L = restricted_objective + Σ_s K_s min(0, δ_s) - numerical_margin

作为有效下界。发现负约化费用路线只说明应加入新列；启发式找不到新列不能说明定价完成。

新增的任务数修正完整枚举每仓所有可行 1/2 任务模式。为每仓构造 β_s ≤ 0，使任意路线 r
均满足 `reduced_cost(r) >= β_s * task_count(r)`：1/2 任务路线直接核对；3 个及以上任务利用
`min(0, δ_s)/3`。所有仓点覆盖的任务总数等于订单数，因此另一个有效下界为

    L_task = restricted_objective + Σ_order min_{s in domain(order)} β_s - numerical_margin

取两者与既有独立下界的最大值。一级与二级费用已经在同一个主问题中，不能再重复加一级距离下界。

`LP_OPTIMAL` / `relaxation_complete` 只表示路线松弛的数值收敛，`proven_optimal` 始终为 False。
混合方案的整数最优性仍由完整 MILP 证明。传入下界约束前以 SHA256 检查模型编号、订单、
日程、车队、仓点服务时长及距离/整数时间矩阵，拒绝其他实例的证书。
根节点耗尽总预算时，只保留通过独立审计的输入可行解，并清除其旧证明和旧下界，
尤其不能从固定分仓初解导入自由分仓的全局证明。

## 实验设计

结果目录：`output/experiments/single_batch_columns_20261001/`。
两个 M2 冻结场景（20、30 顾客），种子 42/43，共同使用上一轮已审计的可行初解。
正式 `mip_final/` 每项总预算 30 秒，根节点上限 10 秒；单线程、顺序独立进程，
保留全部运行，不重试失败。每对基线和混合方案使用相同输入、初解和种子。
基线是带共同初解的完整联合 MILP 内核，不包括分仓初解/固定分仓预热的外层流程。
输入、源码、初解、运行参数、求解日志及独立审计结果全部保留。

`equal_budget/` 是旧版标签定价及仅按仓点趟数修正的探索对照。
该阶段第一对运行曾与单独的长时根节点探针重叠，不能作为干净的耗时对照；原记录保留。
`root_20_long.json`、`root_30_long.json` 是标签定价诊断；`root_30_mip.json` 是 MILP 定价诊断。
首次 MILP 定价因默认整数容差下的微小分数弧导致约化费用核对失败，保存于
`mip_debug_error.txt`；改为更严格整数容差并按对偶费用尺度核对后，进入新的正式实验。

## 重现

```python
from models.routing.solvers.exact.single_batch_columns import root_route_bound, solve_with_route_bound

certificate = root_route_bound(problem, time_limit=30, pricing_method='label')
result = solve_with_route_bound(problem, time_limit=60, root_time=10,
                               pricing_method='mip', initial_result=audited_start)
```

```sh
.venv/bin/python experiments/runs/run_single_batch_columns.py \
  --frozen-runs \
  output/experiments/single_batch_site_flow_20261001/final/zhou_Set2_100-5-1b_subset20_M2 \
  output/experiments/single_batch_site_flow_20261001/final/zhou_Set2_100-5-1b_subset30_M2 \
  --seeds 42 43 --total-time 30 --root-time 10 --pricing-method mip \
  --output output/experiments/single_batch_columns_new_run

.venv/bin/python -m pytest tests/routing/test_single_batch_columns.py -q
```

## 正式结果与默认决策

正式结果在实验结束后从 `mip_final/comparison.json` 汇总，未经逐项筛选。
代码保留为显式实验入口，生产默认不启用。

### 30 秒正式对照（单趟 MILP 定价）

| 顾客数 / 种子 | MILP 距离 | 混合距离 | MILP 下界 | 混合下界 | MILP Gap | 混合 Gap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 20 / 42 | 547.890 | 661.182 | 459.592 | 497.044 | 16.12% | 24.82% |
| 20 / 43 | 650.930 | 658.600 | 458.412 | 497.044 | 29.58% | 24.53% |
| 30 / 42 | 806.923 | 783.294 | 515.791 | 487.678 | 36.08% | 37.74% |
| 30 / 43 | 811.176 | 791.645 | 511.158 | 494.773 | 36.99% | 37.50% |

8/8 通过独立审计与两周期重放。距离 2 改善、2 退步，Gap 1 改善、3 退步，最优证明 0→0。
20 顾客下界提升，但原始可行解更差；30 顾客两种子得到更短路线，但 10 秒定价后留下的界较弱。
不能由下界模型完整求解时的 609.47 宣称 10 秒混合流程已经取得该下界。

### 60 秒补充对照（30 顾客 M2，根节点上限 30 秒）

| 种子 | MILP 距离 | 混合距离 | MILP 下界 | 混合下界 | MILP Gap | 混合 Gap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 42 | 765.264 | 745.644 | 515.791 | 609.456 | 32.60% | 18.26% |
| 43 | 725.575 | 749.964 | 511.158 | 609.456 | 29.55% | 18.74% |

4/4 通过独立审计与两周期重放。两个种子的混合流程均完成路线松弛收敛，根节点耗时 29.71 / 29.39 秒。
路线松弛值为 609.473821542597，经过两层保守数值修正，可用下界为 609.455567233892。
Gap 两组改善，但距离 1 改善、1 退步，最优证明仍为 0→0。说明较长预算下下界有价值，
尚未证明稳定加快全局最优性证明，也未证明最终可行解稳定改善。

### 默认决策与下一步

生产默认维持现有精确求解器。新代码作为 M1/M2 可选研究入口保留，不自动推广到 M3/M4。
这次实现的是路线松弛列生成及其证书与 MILP 的混合方案，不是完整分支定价。
值得后续研究的是更快定价、对偶稳定化、从路线池构造并独立审计更好的完整日程初解，
以及将实际供货时序和有限实体车辆排程引入路线主问题；再决定是否开发完整分支定价。

### 验证完成

88 项专项检查通过；全库 **967 passed**，14 条既有 matplotlib/pyparsing 弃用警告，耗时 51.78 秒。
专项验证包括独立路线全排列、原始模型整数枚举、完整列枚举 LP 对照、未完成定价下界、
有向非度量矩阵、重复客户地址、零资源维度、错误实例证书、两种定价一致性及初解证明隔离。
本轮 5 个源码与 60 秒实验源码快照一致，源码和专项测试在全库回归期间未变化。
正式 30/60 秒实验共 12/12 通过独立审计及两周期重放，未经选择地保留全部结果。
详细哈希及验证汇总见 `output/experiments/single_batch_columns_20261001/validation.json`。
