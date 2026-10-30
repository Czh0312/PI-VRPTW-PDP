# M9 精确求解

数学定义见 [自由选仓多批次模型9](../../../../docs/model9_free_multibatch.md)。
M9 保留 M8 的不等长日历、逐批截点、有限箱池和完整成本，
加入 M4 的顾客级自由选仓；同一顾客全部订单的配送、回收保持同仓。

公开入口 `solve_exact` 和 `model.build_model` 建立完整 K 域的一个 Gurobi MILP。
`model.build_model(batch_count=k)` 与 `milp_solver.solve_milp(problem.for_count(k))`
仅用于固定 K 诊断。全部编号求解代码均由本目录拥有，不调用 M4/M8 求解模块。
当前 M9 只登记 exact。

| 文件 | 职责 |
| --- | --- |
| `parameters.py` | 输入、车队、所有允许仓上的安全时间界 |
| `variables.py` | 顾客选仓、订单仓批、路线、日历与库存变量 |
| `constraints.py` | 同仓、仓批联动、路径容量、供货、截点回运与清洗库存 |
| `objective.py` | 与 M8 相同的完整成本 |
| `formulation.py` | 一个固定 K 的完整业务块 |
| `model.py` | 完整 K 域或固定 K 建模入口 |
| `solver.py` | 单次 Gurobi 精确求解、完整域证明 |
| `milp_solver.py` | 固定 K 诊断 |
| `solution.py` | 先确定分仓和日历，再投影并独立审计 |

结果的 `assignment` 是顾客到前置仓的映射，`order_batch_assignment` 是订单到批次的映射。
只有 Gurobi 完整模型证明最优且路线/成本/连续两周期审计通过时，才保留 `proven_optimal=true`。
