# 当前编号模型

| 目录 | 业务模型 | 批次 |
| --- | --- | --- |
| `model_1/` | 固定分仓、纯两级配送 | B=1 |
| `model_2/` | 自由选仓、纯两级配送 | B=1 |
| `model_3/` | 固定分仓、容器循环 | B=1 |
| `model_4/` | 自由选仓、容器循环 | B=1 |
| `model_5/` | 固定分仓、有限容器跨批循环 | B>1 |

每个模型有独立 `model.py`、`exact/`、`alns/` 与 `__init__.py`。
`../service.py` 真正经这些入口分流，只加载选定的求解方式。
精确求解实现位于 `../solvers/exact/`，ALNS 实现位于 `../solvers/alns/`，二者不互相导入。
`../common/cycle/` 保存共用输入、业务规则、结果格式与独立审计。
M2/M4 的精确算法是分仓枚举；其 `exact/model.py` 暴露 `build_subproblem`，并非一个新增的联合选仓 MILP。

新版 M5 是多批次模型，与已归档的旧 M5 中转模型不同。
历史 M1–M4 只在 `../baselines/`。当前目录不再嵌套 `model_3/multibatch/` 或 `single_batch/`。
详见 [编号与布局](../../../docs/model_layout.md)。
