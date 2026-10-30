# M4 直接 Gurobi 求解

`solver.py` 只调用直接 Gurobi；`strategy="milp"` 为默认，`auto` 为兼容别名。

| 文件 | 职责 |
| --- | --- |
| `model.py` | 完整数学模型入口 |
| `formulation.py` | 业务约束组装 |
| `parameters.py`、`variables.py` | 输入、变量界与决策变量 |
| `constraints.py`、`objective.py` | 业务约束与目标 |
| `milp_solver.py` | 直接求解、预算和求解器证据 |
| `network_strengthening.py` | 自由选仓载荷流、送取先后冲突与兼容趟数排序的车辆对称约束 |
| `solution.py` | 路线和业务决策提取 |
| `solver.py`、`__init__.py` | 公开求解入口 |

`preprocessing/model_bounds.py` 与 `domains.py` 仅提供数学模型变量界、合法弧和分仓域。

默认 `network_strengthening=True`：共享二级内部弧的 `compact` 表达，使用跨所有候选仓的
配送/回收载荷流替代二级大M载荷传播，并加入逐趟启用容量联系、送取先后冲突及时间提升。
所有仓的分配变量和全部原物理选择仍由同一个完整MILP决策；不复用M3的固定分仓下界。
借鉴M8的首任务排序，但仅对趟数相同的同型车辆排序，兼容M4已有的趟数主排序。
显式 `milp_options` 中的 `expanded`/`compact` 请求均受尊重。

`solve_exact(..., network_strengthening=False)` 恢复原默认表达与求解方式；
`build_model` 的该选项默认False，供独立构模和消融。已有 `strengthening` 开关仍独立有效。
结果的 `info.solver_config.network_strengthening` 保存实际载荷流、冲突和排序行数。
有效性及单例验证口径见[网络加速说明](../../../../docs/model4_network_acceleration.md)。

不包含构造初解、自定义割、列生成、Benders、域树或精确 K 调度。
已退役算法及其专用测试/驱动见[研究归档](../../../../archive/exact_research_20261019/README.md)。
配置见[直接 Gurobi](../../../../docs/exact_milp_comparison.md)，数学表达见
[统一批次数 MILP](../../../../docs/unified_batch_count_milp.md)。
