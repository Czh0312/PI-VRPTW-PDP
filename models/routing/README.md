# 两级路径求解器

| 模型 | 精确求解 | ALNS |
| --- | --- | --- |
| 1 | [固定分仓纯配送](model_1/exact/) | [模型 1](model_1/alns/) |
| 2 | [自由分仓纯配送](model_2/exact/) | [模型 2](model_2/alns/) |
| 3 | [固定分仓单批循环](model_3/exact/) | [模型 3](model_3/alns/) |
| 4 | [自由分仓单批循环](model_4/exact/) | [模型 4](model_4/alns/) |
| 5 | [可变批次 MILP](model_5/exact/) | [模型 5](model_5/alns/) |
| 6 | [无批次 MILP](model_6/exact/) | [模型 6](model_6/alns/) |

`catalog.py` 登记公开编号，`service.py` 加载相应算法。模型 5 的 MILP 在同一次求解内选择批次数，ALNS 按候选批次数搜索。模型 6 通过独立物理账本跟踪箱池，精确求解支持 HiGHS 和 Gurobi。

## ALNS 框架

六个模型共用 `run_search`。模型先构造并评价初始解，再在每轮提出并评价候选。引擎先记录符合模型条件的最好解，再按接受规则决定是否替换当前解，最后更新算子权重。引擎检查时间和迭代预算。[主循环](alns/engine.py) · [退火与算子选择](alns/search_control.py)

| 模型 | 接入方式 |
| --- | --- |
| M1–M4 | 共用单批次策略。各模型接入初解构造、路线算子、评价缓存和局部下降。M2 在多个分仓方案间分配预算，M4 在同一条搜索轨迹内尝试迁仓。[单批次策略](alns/single_batch.py) |
| M5 | 外层按批次数 K 分配预算。固定日历与可变日历共用分批、截点和路线算子，可变日历另外尝试移动批次边界。[M5 实现](model_5/alns/) |
| M6 | 使用无批次初解、成对的破坏与修复算子，以及路线和复用调整。评价考虑实际送取、清洗和箱池。[M6 实现](model_6/alns/) |

调整初解时从模型目录的 `construction.py` 开始，调整邻域时从 `neighborhoods.py` 开始。接入新模型时，沿用 `solve_alns` 入口，把已评分的初解、候选生成与评价、最好解判定和接受规则传给 `run_search`。模型入口负责返回解和独立审计。

[编号与调度规则](../../docs/model_layout.md)
