# 路径求解模型

- `common/`：所有模型共用的领域对象、数据/矩阵读取、任务构建和精确求解工具。
- `alns/`：公共 ALNS 数据结构、搜索引擎、强初始解，以及 Model 1–7 的专属算子。
- `variants/model_1` 至 `variants/model_7`：当前完整数学模型与精确求解入口。
- `catalog.py`：模型编号、slug 与含义的唯一来源。
- `service.py`：正式入口使用的统一求解分发层；精确求解器按需加载。

Model 1–7 统一使用 `model.py` 表示完整模型、`exact_solver.py` 表示严格精确入口。
Model 7 采用完整分仓枚举与松弛证书，固定分仓后调用完整 Model 6 子问题。
M2/M3/M4 的默认精确入口使用 `common/commodity_flow.py` 的完整四类货物流模型；
原车辆索引模型通过 `backend="indexed"` 保留。M1 默认继续使用原模型。
M7 可利用全局松弛同时筛除多个不能改善的分仓，仍保留未闭合子问题的有效下界。
详见项目 `docs/solver_optimization_20260913.md` 及对应结果总表。
大型模型的必要约束与目标组件可放入 `formulation/`。过去尝试的列生成、分支定价、
时间扩展和分解算法已移至 `archive/models/routing/variants/`，不再混入当前主线。
