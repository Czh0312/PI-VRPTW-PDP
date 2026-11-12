# M10 ALNS

公开入口 `solve_alns(problem,max_time=120,max_iterations=5000,seed=42,initial_result=None)`。

`state.py` 定义路线、车辆和清洗等待；`construction.py` 构造初解；`evaluator.py` 排时并调用独立审计；`neighborhoods.py` 实现破坏、修复和路线调整；`solver.py` 管理权重、退火和可行最优解；`warm_start.py` 接收已审计M10解。

该目录不导入精确模型、Gurobi、SciPy或其他编号模型算法。规则与实验解释见[模型10说明](../../../../docs/model10_no_batch.md)。
