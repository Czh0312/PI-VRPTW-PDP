# M5 显式混合求解

`solver.py` 组合本模型 ALNS、可行路线池改进和下界证书，共享一次总预算。
现有公开调用 `models.routing.model_5.exact.solve_hybrid` 保持可用，函数调用时才加载这里的实现。
普通 `solve_exact`、`strategy='milp'` 和 ALNS 入口不会隐式执行混合流程。

路线池提供经过独立审计的可行上界；下界由完整定价域证书给出。受限池求解最优不能作为完整原问题的最优证明。
