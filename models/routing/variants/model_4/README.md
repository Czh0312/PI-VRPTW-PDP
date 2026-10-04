# 模型 4：自由选仓、单批次容器循环

范围：`single_batch`；包装：`reusable`；分仓：`free`。

- `model.py`：本模型定义、适用范围校验和旧建模 API 的按需转发。
- `exact/`：精确求解文件夹；`model.py` 建模，`solver.py` 求解。
- `alns/`：ALNS 文件夹；`solver.py` 求解。
- `__init__.py`：公共 API，供 `models/routing/service.py` 调用。

精确算法枚举顾客到仓的分配，每个分配使用共同 MILP；只有全部组合都有证明才能报告全局最优。
`exact/model.py` 的 `build_subproblem` 只构建一个分仓子问题，不能单独证明自由选仓最优。

精确实现位于 `../../solvers/exact/`，ALNS 实现位于 `../../solvers/alns/`。
公共日程、容器账本、分仓转换与独立审计位于 `../../common/cycle/`。
两种求解方式按需加载，互不导入；ALNS 不依赖 Gurobi。
当前目录是真正的编号入口；不依赖其他编号模型的文件夹，也不调用历史基线。
