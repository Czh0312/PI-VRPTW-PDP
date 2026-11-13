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

[编号与调度规则](../../docs/model_layout.md)
