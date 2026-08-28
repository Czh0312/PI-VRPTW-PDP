# 路径求解模型

- `common/`：所有模型共用的领域对象、数据/矩阵读取、任务构建和精确求解工具。
- `alns/`：公共 ALNS 数据结构、搜索引擎、强初始解，以及 Model 1–6 的专属算子。
- `variants/model_1` 至 `variants/model_6`：当前完整数学模型与精确求解入口。
- `variants/model_7`：暂不整理，保留原有 ALNS 实现。
- `catalog.py`：模型编号、slug 与含义的唯一来源。
- `service.py`：正式入口使用的统一求解分发层；精确求解器按需加载。

Model 1–6 统一使用 `model.py` 表示完整模型、`exact_solver.py` 表示严格精确入口。
大型模型的必要约束与目标组件可放入 `formulation/`。过去尝试的列生成、分支定价、
时间扩展和分解算法已移至 `archive/models/routing/variants/`，不再混入当前主线。
