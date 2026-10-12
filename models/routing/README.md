# 八个模型独立维护

```text
routing/
├── model_1/
│   ├── alns/          M1 自己的搜索、算子、评价和缓存
│   └── exact/         M1 自己的建模、求解、初解和下界
├── model_2/ … model_8/     同样各有 alns/、exact/
├── catalog.py        编号与入口登记
└── service.py        选择编号和求解方法
```

**求解代码按模型隔离，不再设置算法 common，也不跨模型调用求解实现。**
相同算法在各模型内是独立普通文件；没有软链接、硬链接、继承转发或自动同步。
修改某个模型目录只修改该模型的实现。以后若要把同一修复用于多个模型，需要逐个明确修改和验证。
ALNS 和精确求解各有自己的实现；M5 的显式混合实验入口会调用本模型的 ALNS，不调用其他模型。

数据读取、业务实例、规则和独立审计仍由 [models/common](../common/README.md) 提供，成本定义在
[models/cost](../cost/README.md)。修改这些基础部分会统一影响相关模型；它们不属于本次拆开的求解算法。
历史基线见 [archive/routing_baselines](../../archive/routing_baselines/README.md)。

| 模型 | 研究含义 | ALNS 文件说明 | 精确文件说明 |
| --- | --- | --- | --- |
| M1 | 固定分仓、一次性包装、单批次纯配送 | [alns](model_1/alns/README.md) | [exact](model_1/exact/README.md) |
| M2 | 自由分仓、一次性包装、单批次纯配送 | [alns](model_2/alns/README.md) | [exact](model_2/exact/README.md) |
| M3 | 固定分仓、单批次循环包装 | [alns](model_3/alns/README.md) | [exact](model_3/exact/README.md) |
| M4 | 自由分仓、单批次循环包装 | [alns](model_4/alns/README.md) | [exact](model_4/exact/README.md) |
| M5 | 固定分仓、多批次循环包装 | [alns](model_5/alns/README.md) | [exact](model_5/exact/README.md) |
| M6 | 等长批次、共同分界、箱池与路线成本 | [alns](model_6/alns/README.md) | [exact](model_6/exact/README.md) |
| M7 | 等长批次、逐批独立分界 | [alns](model_7/alns/README.md) | [exact](model_7/exact/README.md) |
| M8 | 不等长批次、逐批独立分界 | [alns](model_8/alns/README.md) | [exact](model_8/exact/README.md) |

每个算法目录：`solver.py` 是带编号校验的公开入口；`engine.py` 是本模型内部求解过程（如果需要）；
精确目录的 `model.py` 是建模入口，`formulation.py` 等文件定义实际变量与约束。
M7/M8 的 `joint_model.py`、`joint_solver.py` 是各自的固定 K 子问题实现，已与 M6 分开；
内部投影仍使用统一的 JointBatchInstance 数据格式，不代表调用 M6 的求解代码。
`__init__.py` 只导出当前目录的入口和编号信息。

```python
from models.routing.model_3.alns import solve_alns
from models.routing.model_3.exact import solve_exact
```

命令行仍使用 `main.py route --model N --solver alns|exact`。旧源码快照不改写；其模块路径按当时目录解释。
