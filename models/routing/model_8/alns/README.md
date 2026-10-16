# M8 — ALNS

本目录拥有独立实现，不导入其他模型的求解代码。相似文件不自动同步。

按 `parameters.py` → `state.py` → `construction.py` → `neighborhoods.py` → `evaluator.py` → `search_control.py` 阅读算法职责，再看求解入口与搜索循环。
完整说明见[ALNS 模型结构](../../../../docs/alns_model_layout.md)。

| 文件 | 用途 |
| --- | --- |
| [parameters.py](parameters.py) | 搜索预算、温度与自适应更新参数类 |
| [state.py](state.py) | 搜索状态复制、缓存键与已有方案转换 |
| [search_control.py](search_control.py) | 算子选择、冷却、接受及权重更新 |
| [batch_search.py](batch_search.py) | 本模型的 K 搜索预算、结果与证明状态汇总 |
| [calendar_search.py](calendar_search.py) | ALNS 批次数搜索入口 |
| [construction.py](construction.py) | 初解、时间表构造、分仓修复及联合热启动 |
| [evaluator.py](evaluator.py) | 完整路线、时间和库存评价及候选缓存、联合成本评价 |
| [joint_solver.py](joint_solver.py) | 本模型固定 K 子问题的求解、搜索或结果提取 |
| [neighborhoods.py](neighborhoods.py) | 订单破坏／修复、路线移动及分批／分界邻域 |
| [search.py](search.py) | M8 不等长日历的单个 K 搜索流程；调用构造与日历邻域 |
| [single_batch_scoring.py](single_batch_scoring.py) | 单批次插入候选的快速评分 |
| [solver.py](solver.py) | 本模型公开求解入口及编号校验 |

默认 K 调度为 `adaptive`：候选超过三个且允许搜索迭代时，前半预算先搜索三个最小 K（其中第一个获得较大份额），随后四分之一预算探索其他未访问 K，最后四分之一集中改善已审计的最好候选。阶段内的构造、复制与审计也占用总预算；未访问或未解决候选完整保留在诊断中，不产生最优或不可行证明。候选少或仅构造时沿用等份调度。Python 入口可传 `budget_strategy='recycle'` 或 `'single_pass'` 复现原等份策略。

`__init__.py` 导出当前算法入口。基础数据与审计的依赖边界见[模型导航](../../README.md)。
