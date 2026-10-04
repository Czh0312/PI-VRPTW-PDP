# M5：ALNS 求解

`solver.py` 是本模型的独立入口，调用 `../../../solvers/alns/` 下的共用实现。
不依赖精确求解或 Gurobi；可行解不声明全局最优。
