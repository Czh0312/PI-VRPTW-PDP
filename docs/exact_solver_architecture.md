# 精确求解架构与结果契约

本仓库把“求得一条可行路线”和“证明了原问题的全局最优”视为不同结果。这个文档
说明精确模块的职责边界，避免在实验汇总或后续开发中把受限模型误记为精确解。

## 统一返回契约

所有精确入口都可通过 `return_info=True` 返回 `(e1_routes, e2_routes, info)`。
`info` 中至少应检查：

| 字段 | 含义 |
| --- | --- |
| `status` | Gurobi（或前置检查）的原始状态 |
| `objective` / `best_bound` / `mip_gap` | 可行 incumbent、下界与 gap；没有 incumbent 时目标值为空 |
| `solution_type` | 此次结果的求解语义 |
| `proven_optimal` | 只有它为 `True` 时才可计为精确解 |

默认的 `solve_exact(...)` 是严格入口：仅在模型未缩减、Gurobi 返回 `OPTIMAL` 时
返回路线并标记 `proven_optimal`。如只需要限时可行解、初始解或诊断结果，使用
`solve_mip(...)`（Model 1/2）或显式允许 incumbent 的受限入口；这些结果绝不应
进入 Exact 平均值或成功率。

## 求解模块

| 模块 | 入口 | 可认证的条件 | 非认证结果 |
| --- | --- | --- | --- |
| Model 1 | `models.model_1.solver.solve_exact` | 原始 MIP `OPTIMAL` | `feasible_not_proven` |
| Model 2 弧流 | `models.model_2.solver.solve_exact` | 原始联合 MIP `OPTIMAL` | `feasible_not_proven` |
| Model 2 路线宇宙 | `route_universe.solve_route_universe_exact` | 每个卫星的路线宇宙完整且 MIP `OPTIMAL` | `ROUTE_UNIVERSE_INCOMPLETE` |
| Model 2 直接路线主问题 | `route_master.solve_route_master_exact` | 同上；E1/E2 联合时间约束仍在主问题中 | `branch_node_optimal` 或受限结果 |
| Model 3 | `models.model_3.solver.solve_exact` | 全部中转候选（`max_relocation_candidates=None`）且 MIP `OPTIMAL` | `restricted_optimal` / `feasible_not_proven` |
| Model 4 | `models.model_4.solver.solve_exact` | 无；固定路线后的两阶段流程不是原问题全局优化 | `two_stage_not_global` |

## Model 2 的路线组件

`route_universe.py` 穷举小规模卫星的容量/时间窗可行 E2 路线。安全上限默认是每个
卫星 10 个任务、250,000 条路线；超过上限时立即停止并报告不完整，不以有限集合
冒充全体路线。

`route_master.py` 是车/时间感知的直接主问题。每个路线列均有独立的发车和返回时刻，
客户有唯一的绝对服务时刻；因此 E1 到货后才能出发、E2 返回后才能由 E1 回收、以及
同一客户的“先送后取”约束仍以联合形式存在。`solve_route_master_pool(...)` 仅优化
调用方给定的有限列池，结果固定为 `restricted_route_pool_optimal`。

`route_pricing.py` 只精确求解**一条** E2 路线的定价子问题，支持弧成本、任务奖励
和 Ryan–Foster `same` / `different` 规则。它尚未接入联合主问题的对偶、列加入循环
和完整分支树，因而不能单独证明 Model 2 的全局最优。

## 当前 MIP 强化

Model 2 的弧流与直接路线主问题使用按实例构造的安全时间上界，而不是固定的
`100000` 时间 Big-M。上界由所有有限时间窗、有效 `TFp`、最长行驶弧和全部任务
服务时间构造；对任一可行路线，按最早可行时刻调度均落在此界内。模型还固定
时间不可行弧、使用弧特定的时间 Big-M，并按 E1/E2 初始配送载重对同质车辆排序。
这些都是有效不等式/变量界：不会删除任何可行解，也不会改变 `proven_optimal` 的
语义。

## 开发与回归

运行最小精确性回归：

```bash
PYTHONPYCACHEPREFIX=/tmp/2e_vrptw_pycache \
  .venv/bin/python experiments/test_exact_contract.py
```

该测试在 `data/5_customers/C101.csv` 上验证：Model 1、Model 2 弧流、完整路线宇宙
和直接路线主问题均认证；两种 Model 2 表达的目标值一致；有限路线池与单个
Ryan–Foster 分支节点不会被认证；定价 oracle 遵守分支规则。

任何修改模型、列定义或状态映射后，都应先运行该测试，再运行目标规模的限时基准；
后者只能报告 `objective`、`best_bound`、`mip_gap` 和 `solution_type`，不能因找到
incumbent 就称为精确。

## 下一步：真正的 branch-price-and-cut

要把路线主问题扩展为可认证的大规模算法，需要实现以下完整闭环：

1. 从受限主问题取得对偶价格，并调用定价 oracle 找到所有负化简成本列；
2. 对每个节点重复“定价至无负列”，使节点的 LP 界有效；
3. 建立并穷尽 Ryan–Foster（或等价）分支树，且把分支规则传给定价；
4. 对联合 E1/E2 时间与容量约束加入必要的割，并证明这些割有效；
5. 只有根节点/全树关闭且全局 gap 为零时，才能返回 `proven_optimal=True`。

在这之前，路线池、单节点闭合和单条路线定价都是有用的计算组件，但都必须保持
非认证状态。
