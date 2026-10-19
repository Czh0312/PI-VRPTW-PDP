# M7 精确求解代码导航

从 `solver.py` 开始：`strategy="milp"` 调用 `milp_solver.py`，默认 `auto` 使用本模型定制流程。
两条路径共用 `formulation.py` 的数学定义和 `solution.py` 的业务解提取。

`enhanced_solver.py` 求解一个固定 K 子问题，`k_search/scheduler.py` 覆盖允许的完整 K 域并汇总证明。未闭合 K 始终保留。

## 职责边界


## 文件导航

| 文件 | 职责 |
| --- | --- |
| [solver.py](solver.py) | 公开求解入口：选择直接 MILP 或定制精确方法 |
| [model.py](model.py) | 公开建模入口，固定 K 子问题选择 |
| [formulation.py](formulation.py) | 实际数学模型组装，基线和增强求解共用 |
| [parameters.py](parameters.py) | 已知参数、运行规则与安全派生界 |
| [variables.py](variables.py) | 决策变量与变量容器 |
| [constraints.py](constraints.py) | 完整业务约束入口 |
| [objective.py](objective.py) | 目标函数与费用表达 |
| [milp_solver.py](milp_solver.py) | 直接 Gurobi 基线：显式配置、预算和原始证据 |
| [enhanced_solver.py](enhanced_solver.py) | 固定 K 下定制增强 MILP 的完整求解流程 |
| [initialization.py](initialization.py) | 初解准备与 MIP Start 编码 |
| [solution.py](solution.py) | 将决策变量解码成路线和业务方案 |
| [k_search/scheduler.py](k_search/scheduler.py) | 完整 K 域的预算分配、候选重访及全局证明汇总 |

公开包调用保持可用。已移除旧内部目录，不保留空目录或转发文件；直接导入内部模块的外部脚本需要更新路径。

参见[目录指南](../../../../docs/solver_directory_guide.md)、[Gurobi 对照说明](../../../../docs/exact_milp_comparison.md)。
