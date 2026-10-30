# M5 — ALNS 阅读导航

当前成本模式在每个候选时间表下重新计算所需实体箱数 N，以包含 N 的总成本评价候选。固定分批和日历，输入与审计规则见[箱池优化说明](../../../../docs/model5_pool_optimization.md)。

先看 `solver.py` 确认入口，再看 **`search.py`** 理解主要迭代流程；遇到构造、邻域、评价和搜索控制时，再进入对应文件。
本目录独立拥有本模型算法，不导入其他编号模型的求解实现。

## 实际调用路径

`solver.py` → `search.py`（正式多批次）。内部 B=1 路径转入 `single_batch_search.py`。

## 核心文件

| 文件 | 用途 |
| --- | --- |
| [solver.py](solver.py) | 公开求解入口、输入校验与搜索分派 |
| [search.py](search.py) | 本模型的主要 ALNS 迭代：选算子、生成候选、评价、接受、更新最好解 |
| [parameters.py](parameters.py) | 时间与迭代预算、温度和自适应权重参数 |
| [state.py](state.py) | 搜索状态的复制、缓存键与已有解转换 |
| [construction.py](construction.py) | 初解构造、方案修复与热启动转换 |
| [neighborhoods.py](neighborhoods.py) | 订单破坏／修复、路线移动；联合模型还包含订单分批移动 |
| [evaluator.py](evaluator.py) | 完整解码、成本与可行性评价、候选评价缓存 |
| [search_control.py](search_control.py) | 算子选择、温度、接受准则与权重更新 |

## 本模型的专门搜索与局部改进

| 文件 | 用途 |
| --- | --- |
| [local_descent.py](local_descent.py) | 单批次局部距离下降；生成改进候选，仍由完整评价确认 |
| [intensification.py](intensification.py) | M5 多批次强化局部搜索 |
| [pool_moves.py](pool_moves.py) | 成本模式的箱池复用候选：移除等待提示、拆出较早回仓的回收前缀；允许距离增加，仍按完整成本评价 |
| [single_batch_search.py](single_batch_search.py) | M5 内部 B=1 专门化搜索；不是多批次失败后的兜底 |

## 缓存与快速计算

| 文件 | 用途 |
| --- | --- |
| [performance/cycle_cache.py](performance/cycle_cache.py) | 完整周期解码中的一级回运缓存 |
| [performance/single_batch_cache.py](performance/single_batch_cache.py) | 单批次评价与路线缓存 |
| [performance/scoring.py](performance/scoring.py) | 插入与路线候选的快速评分 |
| [performance/timing.py](performance/timing.py) | 数组化时间约束传播 |

快速评分用于候选排序或筛选；完整评价和独立业务审计决定可行性与最终成本。
成本模式每九次迭代至多尝试一次箱池复用候选；无可用候选时继续原下降搜索。
候选队列按当前最好状态重建、最多64项，遵守物理车辆趟次上限和原时间预算。
E1 回收插入复用配送前缀及未改变的弧值，保留原距离累加顺序；单回收站直接评价。
局部下降、强化和精修属于搜索策略，因此放在顶层；`performance/` 收纳缓存和快速计算。

日常调用仍使用 `from models.routing.model_5.alns import solve_alns`。
`__init__.py` 导出公开入口；内部模块路径随本次整理调整。
完整职责与迁移说明见 [ALNS 模型结构](../../../../docs/alns_model_layout.md)；
两类算法的整体阅读入口见 [目录指南](../../../../docs/solver_directory_guide.md)。
