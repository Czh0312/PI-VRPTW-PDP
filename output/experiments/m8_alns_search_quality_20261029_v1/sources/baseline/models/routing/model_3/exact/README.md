# M3 直接 Gurobi 求解

`solver.py` 只调用直接 Gurobi；`strategy="milp"` 为默认，`auto` 为兼容别名。

| 文件 | 职责 |
| --- | --- |
| `model.py` | 完整数学模型入口 |
| `formulation.py` | 业务约束组装 |
| `parameters.py`、`variables.py` | 输入、变量界与决策变量 |
| `constraints.py`、`objective.py` | 业务约束与目标 |
| `milp_solver.py` | 直接求解、预算和求解器证据 |
| `route_partition.py` | 小规模输入型车次下界及新生成的完整调度初解 |
| `solution.py` | 路线和业务决策提取 |
| `solver.py`、`__init__.py` | 公开求解入口 |

`preprocessing/model_bounds.py` 与 `domains.py` 仅提供数学模型变量界、合法弧和分仓域。

最多9个订单时，默认 `route_strengthening=True`：从当前输入完整枚举放松的二级车次任务集合，
求整数集合划分下界，并把有效成本下界加入完整 MILP。成本模式再以新选出的车次集合限制一个
完整模型副本，仅用于生成内部初解；原模型仍保留所有路线、车辆、趟位和整数时间选择。
最后的最优状态、上下界及业务审计全部来自未限制的完整模型。

下界准备与初解阶段各最多使用当时剩余预算的25%，且各不超过5秒；全部计入总预算。
超过规模范围或枚举未完成时，丢弃不完整车次库并回到原模型。
不读取历史方案、历史上下界或实验目录。`info.route_strengthening`、`internal_start` 和
`preparation_time` 分别记录新下界、内部初解与总准备开销，`mip_start_supplied` 如实标记实际初解。
公开 Python 入口 `solve_exact(..., route_strengthening=False)` 可复现原冷启动基线；
`build_model` 仍只构建原完整模型，不隐式求解下界或构造初解。

不包含自定义回调、列生成、Benders、域树或精确 K 调度。
已退役算法及其专用测试/驱动见[研究归档](../../../../archive/exact_research_20261019/README.md)。
配置见[直接 Gurobi](../../../../docs/exact_milp_comparison.md)，数学表达见
[统一批次数 MILP](../../../../docs/unified_batch_count_milp.md)。
