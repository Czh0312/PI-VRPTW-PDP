# M8 — ALNS 阅读导航

先看 `solver.py` 确认入口，再看 **`search.py`** 理解主要迭代流程；遇到构造、邻域、评价和搜索控制时，再进入对应文件。
本目录独立拥有本模型算法，不导入其他编号模型的求解实现。

## 实际调用路径

`solver.py` → `k_search/scheduler.py` → `search.py`（给定 K，联合改变日历边界、分批、批内分界与路线）。K=1 和小实例热启动使用 `fixed_calendar_search.py`。

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
| [polish.py](polish.py) | 路线重分段、车辆与配送／回收联合精修 |
| [cutoff_moves.py](cutoff_moves.py) | 批内分界移动、事件候选与有限局部精修 |
| [batch_initialization.py](batch_initialization.py) | 均衡合法分批的初解构造 |
| [calendar_moves.py](calendar_moves.py) | 给定 K 下，不等长批次日历边界的移动与事件候选 |
| [calendar_polish.py](calendar_polish.py) | 日历边界、订单分批与路线的联合精修 |
| [fixed_calendar_search.py](fixed_calendar_search.py) | 固定日历边界下优化订单分批、批内分界与路线；用于 K=1 和热启动 |
| [k_search/scheduler.py](k_search/scheduler.py) | 跨 K 分配时间与迭代预算、复用已审计解并汇总搜索结果 |

## 缓存与快速计算

| 文件 | 用途 |
| --- | --- |
| [performance/scoring.py](performance/scoring.py) | 插入与路线候选的快速评分 |

快速评分用于候选排序或筛选；完整评价和独立业务审计决定可行性与最终成本。
局部下降、强化和精修属于搜索策略，因此放在顶层；`performance/` 收纳缓存和快速计算。

## 现有搜索策略

默认 K 调度为 `adaptive`：候选超过三个且允许搜索迭代时，前半预算先搜索三个最小 K（其中第一个获得较大份额），随后四分之一预算探索其他未访问 K，最后四分之一集中改善已审计的最好候选。阶段内的构造、复制与审计也占用总预算；未访问或未解决候选完整保留在诊断中，不产生最优或不可行证明。候选少或仅构造时沿用等份调度。Python 入口可传 `budget_strategy='recycle'` 或 `'single_pass'` 复现原等份策略。

16 个订单以内，在同一总预算内预留路线改进时间。重分段允许上一批回收与下一批配送合并，联合移动可同时改变 E1 配送及回收车辆，并重新优化回收截点。每个候选都经过完整时间、数量和周期库存重放；只接收真实总成本下降。算法不读取参考最优解，不产生全局最优证明。

日常调用仍使用 `from models.routing.model_8.alns import solve_alns`。
`__init__.py` 导出公开入口；内部模块路径随本次整理调整。
完整职责与迁移说明见 [ALNS 模型结构](../../../../docs/alns_model_layout.md)；
两类算法的整体阅读入口见 [目录指南](../../../../docs/solver_directory_guide.md)。
