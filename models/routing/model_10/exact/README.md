# M10 精确求解

公开入口 `solve_exact(problem,time_limit=60,backend='highs')`。`highs` 通过SciPy调用；`gurobi` 需要有效许可证。二者使用同一完整MILP，不调用ALNS。

阅读顺序：`formulation.py`（表达）、`linear.py`（稀疏线性模型和后端）、`solution.py`（提取）、`solver.py`（预算、证明状态、审计）。公开构建接口为 `model.build_model`。

规则、复用链、单前置仓规范化及示例见[模型10说明](../../../../docs/model10_no_batch.md)。
