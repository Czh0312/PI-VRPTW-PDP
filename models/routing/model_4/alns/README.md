# M4 — ALNS

本目录拥有独立实现，不导入其他模型的求解代码。相似文件不自动同步。

| 文件 | 用途 |
| --- | --- |
| [assignment.py](assignment.py) | 循环包装分仓搜索与连续迁仓算子 |
| [construction.py](construction.py) | 初始路线和跨批时间表构造 |
| [cycle_cache.py](cycle_cache.py) | 单次求解内的一级回运解码缓存 |
| [evaluator.py](evaluator.py) | 完整路线、时间和库存的候选解评价 |
| [neighborhoods.py](neighborhoods.py) | 订单破坏／修复、候选缓存和热启动转换 |
| [single_batch.py](single_batch.py) | 单批次 ALNS 搜索 |
| [single_batch_cache.py](single_batch_cache.py) | 单批次任务与路线距离缓存 |
| [single_batch_descent.py](single_batch_descent.py) | 单批次局部距离下降 |
| [single_batch_scoring.py](single_batch_scoring.py) | 单批次插入候选的快速评分 |
| [single_batch_timing.py](single_batch_timing.py) | 数组化时序约束传播 |
| [solver.py](solver.py) | 本模型公开求解入口及编号校验 |

`__init__.py` 导出当前算法入口。基础数据与审计的依赖边界见[模型导航](../../README.md)。
