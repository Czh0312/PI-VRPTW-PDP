# 模型 2：自由选仓、纯两级配送（一次性包装对照）

范围：`single_batch`；包装：`single_use`；分仓：`free`。

- `model.py`：本模型定义、适用范围校验和旧建模 API 的按需转发。
- `exact/`：精确求解文件夹；`model.py` 建模，`solver.py` 求解。
- `alns/`：ALNS 文件夹；`solver.py` 求解。
- `__init__.py`：公共 API，供 `models/routing/service.py` 调用。

精确算法按规模自动选择联合顾客分仓 MILP 或分仓域树，完整枚举保留为显式对照。
`exact/model.py` 提供 `build_joint_model` 与固定分仓 `build_subproblem`。全局证明来自联合模型，
或由最优、不可行证明及有效界闭合的全部分仓域；单个固定分仓子问题不能单独证明自由选仓最优。

精确实现位于 `../../solvers/exact/`，ALNS 实现位于 `../../solvers/alns/`。
公共日程、容器账本、分仓转换与独立审计位于 `../../common/cycle/`。
两种求解方式按需加载，互不导入；ALNS 不依赖 Gurobi。
当前目录是真正的编号入口；不依赖其他编号模型的文件夹，也不调用历史基线。
