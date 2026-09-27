# 路径模型目录

Model 1–8 使用统一结构：

- `model.py`：当前采用的完整数学模型及其构造/解提取逻辑。
- `exact_solver.py`：严格精确求解入口；只有满足证明条件时才报告全局最优。
- `formulation/`：仅在模型较大时拆出的必要约束和目标组件，不是备选算法。

模型专属 ALNS 位于 `../alns/variants/`。历史列生成、分支定价、时间扩展、拉格朗日
分解和探针代码位于 `archive/models/routing/variants/`。Model 7 同样使用
`model_7/model.py`、`model_7/exact_solver.py`；对应 ALNS 位于
`../alns/variants/model_7.py`。

| 模型 | 完整模型 | 精确入口 | 必要组件 |
| --- | --- | --- | --- |
| Model 1 | `model_1/model.py` | `model_1/exact_solver.py` | `model_1/formulation/` |
| Model 2 | `model_2/model.py` | `model_2/exact_solver.py` | — |
| Model 3 | `model_3/model.py` | `model_3/exact_solver.py` | `model_3/formulation/` |
| Model 4 | `model_4/model.py` | `model_4/exact_solver.py` | — |
| Model 5 | `model_5/model.py` | `model_5/exact_solver.py` | — |
| Model 6 | `model_6/model.py` | `model_6/exact_solver.py` | — |
| Model 7 | `model_7/model.py` | `model_7/exact_solver.py` | — |
| Model 8 | `model_8/model.py` | `model_8/exact_solver.py` | `model_8/state.py` |

M8 v3 为 E1/E2 路线、配送回收、空箱交接联合问题；不先求 M3，不固定配送骨架。
精确入口仅对完整问题报告 `proven_optimal`。现有规则下其精确可行域与 M6 相同。
历史两阶段版本位于 `model_8/two_stage_v2/`，更早版本位于 `model_8/legacy/`。
