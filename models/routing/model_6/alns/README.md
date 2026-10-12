# M6 — ALNS

本目录拥有独立实现，不导入其他模型的求解代码。相似文件不自动同步。

按 `parameters.py` → `state.py` → `construction.py` → `neighborhoods.py` → `evaluator.py` → `search_control.py` 阅读算法职责，再看求解入口与搜索循环。
完整说明见[ALNS 模型结构](../../../../docs/alns_model_layout.md)。

| 文件 | 用途 |
| --- | --- |
| [parameters.py](parameters.py) | 搜索预算、温度与自适应更新参数类 |
| [state.py](state.py) | 搜索状态复制、缓存键与已有方案转换 |
| [search_control.py](search_control.py) | 算子选择、冷却、接受及权重更新 |
| [batch_search.py](batch_search.py) | 本模型的 K 搜索预算、结果与证明状态汇总 |
| [construction.py](construction.py) | 初解、时间表构造、分仓修复及联合热启动 |
| [evaluator.py](evaluator.py) | 完整路线、时间和库存评价及候选缓存、联合成本评价 |
| [neighborhoods.py](neighborhoods.py) | 订单破坏／修复、路线移动及分批／分界邻域 |
| [single_batch_scoring.py](single_batch_scoring.py) | 单批次插入候选的快速评分 |
| [solver.py](solver.py) | 本模型公开求解入口及编号校验 |

`__init__.py` 导出当前算法入口。基础数据与审计的依赖边界见[模型导航](../../README.md)。
