# M8 — 精确求解

本目录拥有独立实现，不导入其他模型的求解代码。相似文件不自动同步。

| 文件 | 用途 |
| --- | --- |
| [batch_search.py](batch_search.py) | 本模型的 K 搜索预算、结果与证明状态汇总 |
| [calendar_search.py](calendar_search.py) | 精确求解的批次数搜索入口 |
| [joint_model.py](joint_model.py) | 本模型固定 K 子问题的联合成本 MILP |
| [joint_solver.py](joint_solver.py) | 本模型固定 K 子问题的求解、搜索或结果提取 |
| [model.py](model.py) | 该编号的 MILP 建模入口 |
| [solver.py](solver.py) | 本模型公开求解入口及编号校验 |

`__init__.py` 导出当前算法入口。基础数据与审计的依赖边界见[模型导航](../../README.md)。
