# 模型 5：固定分仓、多批次有限容器循环

范围：`multi_batch`；包装：`reusable`；分仓：`fixed`。

- `model.py`：本模型定义、适用范围校验和旧建模 API 的按需转发。
- `exact/`：精确求解文件夹；`model.py` 建模，`solver.py` 求解。
- `alns/`：ALNS 文件夹；`solver.py` 求解。
- `__init__.py`：公共 API，供 `models/routing/service.py` 调用。

`exact/model.py` 的 `build_model` 构建完整固定分仓 MILP。

这是当前多批次模型 M5，要求 B>1。原来暂放在 M3/multibatch 的实现已按业务规则和求解方式拆分；旧 M5 中转模型没有恢复。

精确实现位于 `../../solvers/exact/`，ALNS 实现位于 `../../solvers/alns/`。
公共日程、容器账本、分仓转换与独立审计位于 `../../common/cycle/`。
两种求解方式按需加载，互不导入；ALNS 不依赖 Gurobi。
当前目录是真正的编号入口；不依赖其他编号模型的文件夹，也不调用历史基线。

ALNS 默认先探索原筛查通过的候选，再以更新的车辆前缀下界补查误筛的任务迁移。
完整 60 对平均距离和平均种子 CV 均改善，部分分组仍退步；详见
[第五轮优化与采用门槛](../../../../docs/model5_alns_round5_20261001.md)。
距离弧缓存继续通过 `cache_descent_distances=True` 显式开启，默认关闭。
