# M3 直接 Gurobi 求解

`solver.py` 只调用直接 Gurobi；`strategy="milp"` 为默认，`auto` 为兼容别名。

| 文件 | 职责 |
| --- | --- |
| `model.py` | 完整数学模型入口 |
| `formulation.py` | 业务约束组装 |
| `parameters.py`、`variables.py` | 输入、变量界与决策变量 |
| `constraints.py`、`objective.py` | 业务约束与目标 |
| `milp_solver.py` | 直接求解、预算和求解器证据 |
| `route_partition.py` | 分仓下界、安全的限时枚举与新生成的完整调度初解 |
| `route_bounds.py` | 多项式弧流下界、容量趟数下界与确定性车次候选 |
| `solution.py` | 路线和业务决策提取 |
| `solver.py`、`__init__.py` | 公开求解入口 |

`preprocessing/model_bounds.py` 与 `domains.py` 仅提供数学模型变量界、合法弧和分仓域。

默认 `route_strengthening=True`，所有订单规模使用同一处理，没有总订单数开关：

- 默认共享二级内部弧（`compact`），保留任务到具体车辆/趟次的归属联系；显式 `expanded` 请求仍受尊重。
- 每趟配送及回收的重量/体积与趟次启用相连，并按原需求与容量向上取整推导各仓必要趟数。
- 按仓先算合法弧、覆盖和必要趟数的LP下界，再在同一准备预算内尝试完整车次枚举。
  完整枚举的仓可用整数集合划分下界；未完成的仓保留LP或解析下界。各仓下界相加仍是全局有效下界。
- 部分枚举只能生成初解候选，不能当作完整车次集合计算下界。确定性时间排序插入法提供候选分组和路径；
  完整模型副本先尝试生成的车辆/路径分配，失败且有预算时解除这些固定，仅保留候选分组再求。

原主模型保留全部原始物理选择。最终最优状态、上下界及业务审计全部来自完整主模型，
不会把候选调度副本或松弛的最优状态当作原问题最优证明。

下界准备与初解阶段各最多使用当时剩余预算的25%，且各不超过5秒；全部计入总预算。
准备预算自动在仓间分配，枚举另受50万搜索前缀的工作量保护；保护不改变业务可行域，
也不需要按7/20/50单重新设置。未完成仓不影响已完成仓的有效下界。
不读取历史方案、历史上下界或实验目录。`info.route_strengthening`、`internal_start` 和
`preparation_time` 分别记录新下界、内部初解与总准备开销，`mip_start_supplied` 如实标记实际初解。
公开 Python 入口 `solve_exact(..., route_strengthening=False)` 可复现原expanded冷启动基线；
`build_model` 仍只构建原完整模型，不隐式求解下界或构造初解。

不包含自定义回调、列生成、Benders、域树或精确 K 调度。
已退役算法及其专用测试/驱动见[研究归档](../../../../archive/exact_research_20261019/README.md)。
配置见[直接 Gurobi](../../../../docs/exact_milp_comparison.md)，数学表达见
[统一批次数 MILP](../../../../docs/unified_batch_count_milp.md)。
通用加强的有效性、限时回退和证明口径见
[不依赖顾客数门槛的通用加强](../../../../docs/model3_general_acceleration.md)。
