# M2 — ALNS

本目录拥有独立实现，不导入其他模型的求解代码。相似文件不自动同步。

按 `parameters.py` → `state.py` → `construction.py` → `neighborhoods.py` → `evaluator.py` → `search_control.py` 阅读算法职责，再看求解入口与搜索循环。
完整说明见[ALNS 模型结构](../../../../docs/alns_model_layout.md)。

| 文件 | 用途 |
| --- | --- |
| [parameters.py](parameters.py) | 搜索预算、温度与自适应更新参数类 |
| [state.py](state.py) | 搜索状态复制、缓存键与已有方案转换 |
| [search_control.py](search_control.py) | 算子选择、冷却、接受及权重更新 |
| [assignment.py](assignment.py) | 纯配送分仓搜索与迁仓算子 |
| [assignment_moves.py](assignment_moves.py) | 整仓关闭与多顾客协调迁仓候选，仍须物理可行性核验 |
| [construction.py](construction.py) | 初解、时间表构造、分仓修复及联合热启动 |
| [cycle_cache.py](cycle_cache.py) | 单次求解内的一级回运解码缓存 |
| [evaluator.py](evaluator.py) | 完整路线、时间和库存评价及候选缓存 |
| [neighborhoods.py](neighborhoods.py) | 订单破坏／修复、路线移动 |
| [partitioned_assignment.py](partitioned_assignment.py) | M2 纯配送的分仓探索与路线热启动 |
| [single_batch.py](single_batch.py) | 单批次 ALNS 搜索流程组装 |
| [single_batch_cache.py](single_batch_cache.py) | 单批次任务与路线距离缓存 |
| [single_batch_descent.py](single_batch_descent.py) | 单批次局部距离下降 |
| [single_batch_scoring.py](single_batch_scoring.py) | 单批次插入候选的快速评分 |
| [single_batch_timing.py](single_batch_timing.py) | 数组化时序约束传播 |
| [solver.py](solver.py) | 本模型公开求解入口及编号校验 |

`__init__.py` 导出当前算法入口。基础数据与审计的依赖边界见[模型导航](../../README.md)。
