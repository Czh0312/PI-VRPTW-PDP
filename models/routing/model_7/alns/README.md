# M7 — ALNS

本目录拥有独立实现，不导入其他模型的求解代码。相似文件不自动同步。

| 文件 | 用途 |
| --- | --- |
| [batch_search.py](batch_search.py) | 本模型的 K 搜索预算、结果与证明状态汇总 |
| [calendar_search.py](calendar_search.py) | ALNS 批次数搜索入口 |
| [construction.py](construction.py) | 初始路线和跨批时间表构造 |
| [evaluator.py](evaluator.py) | 完整路线、时间和库存的候选解评价 |
| [joint_solver.py](joint_solver.py) | 本模型固定 K 子问题的求解、搜索或结果提取 |
| [neighborhoods.py](neighborhoods.py) | 订单破坏／修复、候选缓存和热启动转换 |
| [single_batch_scoring.py](single_batch_scoring.py) | 单批次插入候选的快速评分 |
| [solver.py](solver.py) | 本模型公开求解入口及编号校验 |

`__init__.py` 导出当前算法入口。基础数据与审计的依赖边界见[模型导航](../../README.md)。
