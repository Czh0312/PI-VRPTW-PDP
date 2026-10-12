# M6 — 精确求解

本目录拥有独立实现，不导入其他模型的求解代码。相似文件不自动同步。

先按 `parameters.py` → `variables.py` → `constraints.py` → `objective.py` 阅读数学定义，
再看组装文件和 `solver.py`。参数类复用公共业务实例，变量类管理同一个 Gurobi 模型。
完整导航见[精确模型结构](../../../../docs/exact_model_layout.md)。

| 文件 | 用途 |
| --- | --- |
| [parameters.py](parameters.py) | 已知参数、安全派生界及参数数据类 |
| [variables.py](variables.py) | 决策变量定义与 `ModelVariables` 分组管理 |
| [constraints.py](constraints.py) | 按业务含义组织的约束组 |
| [objective.py](objective.py) | 距离或完整成本目标函数 |
| [batch_search.py](batch_search.py) | 本模型的 K 搜索预算、结果与证明状态汇总 |
| [model.py](model.py) | 完整联合分批、箱池与两级路线成本 MILP 组装 |
| [solver.py](solver.py) | 本模型公开求解入口及编号校验 |

`__init__.py` 导出当前算法入口。基础数据与审计的依赖边界见[模型导航](../../README.md)。
