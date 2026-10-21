# M5 直接 Gurobi 求解

当前 M5 在固定分批和日历下联合优化整数箱数 N 与调度总成本，与 M6–M8 使用相同费用定义。输入、结果及历史诊断兼容见[箱池优化说明](../../../../docs/model5_pool_optimization.md)。

`solver.py` 只调用直接 Gurobi；`strategy="milp"` 为默认，`auto` 为兼容别名。

| 文件 | 职责 |
| --- | --- |
| `model.py` | 完整数学模型入口 |
| `formulation.py` | 业务约束组装 |
| `parameters.py`、`variables.py` | 输入、变量界与决策变量 |
| `constraints.py`、`objective.py` | 业务约束与目标 |
| `milp_solver.py` | 直接求解、预算和求解器证据 |
| `solution.py` | 路线和业务决策提取 |
| `solver.py`、`__init__.py` | 公开求解入口 |

`constraint_blocks/` 按分批、路线、两级车辆、回收和库存拆分完整业务约束。

不包含构造初解、自定义割、列生成、Benders、域树或精确 K 调度。
已退役算法及其专用测试/驱动见[研究归档](../../../../archive/exact_research_20261019/README.md)。
配置见[直接 Gurobi](../../../../docs/exact_milp_comparison.md)，数学表达见
[统一批次数 MILP](../../../../docs/unified_batch_count_milp.md)。
