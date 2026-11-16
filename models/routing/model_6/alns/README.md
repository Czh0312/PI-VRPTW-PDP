# M6 ALNS

公开入口 `solve_alns(problem,max_time=120,max_iterations=5000,seed=42,initial_result=None)`。

`solver.py` 将初解、候选评价和无批次接受策略接入[共用 ALNS 主循环](../../alns/engine.py)。

- [state.py](state.py) 定义路线、车辆和清洗等待。
- [construction.py](construction.py) 构造初解，[warm_start.py](warm_start.py) 读取已审计的 M6 解。
- [neighborhoods.py](neighborhoods.py) 实现破坏、修复、路线和复用调整。
- [evaluator.py](evaluator.py) 排时、评价箱池和运输成本，并调用独立审计。

[无批次模型规则](../../../../docs/model6_no_batch.md)
