# Model 5：空容器单次接力

- `model.py`：完整联合 MIP、接力约束、热启动和解提取。
- `exact_solver.py`：证书优先、必要时回退完整联合 MIP 的精确入口。

早期拆分式 `model/constraints/objective` 建模尝试已归档。
