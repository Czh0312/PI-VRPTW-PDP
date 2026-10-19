# M1 精确求解代码导航

从 `solver.py` 开始：`strategy="milp"` 调用 `milp_solver.py`，默认 `auto` 使用本模型定制流程。
两条路径共用 `formulation.py` 的数学定义和 `solution.py` 的业务解提取。

## 职责边界

- `preprocessing/`：安全时间界、删弧与分仓域。
- `bounds/`：下界、松弛和定价证书。松弛最优不等于原问题最优。
- `branch_cut/`：完整 MILP 的有效不等式及容量割；不是独立的完整分支定价算法。
- `primal/`：构造或改进可行上界，进入求解结果前需独立审计。

## 文件导航

| 文件 | 职责 |
| --- | --- |
| [solver.py](solver.py) | 公开求解入口：选择直接 MILP 或定制精确方法 |
| [model.py](model.py) | 公开建模入口、模型编号校验及建模选项转换 |
| [formulation.py](formulation.py) | 实际数学模型组装，基线和增强求解共用 |
| [parameters.py](parameters.py) | 已知参数、运行规则与安全派生界 |
| [variables.py](variables.py) | 决策变量与变量容器 |
| [constraints.py](constraints.py) | 完整业务约束入口 |
| [objective.py](objective.py) | 目标函数与费用表达 |
| [milp_solver.py](milp_solver.py) | 直接 Gurobi 基线：显式配置、预算和原始证据 |
| [enhanced_solver.py](enhanced_solver.py) | 定制增强 MILP 的完整求解流程 |
| [initialization.py](initialization.py) | 初解准备与 MIP Start 编码 |
| [solution.py](solution.py) | 将决策变量解码成路线和业务方案 |
| [preprocessing/domains.py](preprocessing/domains.py) | 客户可选仓点与安全时间域 |
| [preprocessing/strengthening.py](preprocessing/strengthening.py) | 单批次安全界、稀疏弧和车辆趟数界 |
| [bounds/distance.py](bounds/distance.py) | 必访节点的保守距离下界 |
| [bounds/route_cg/columns.py](bounds/route_cg/columns.py) | 单批次路线列生成下界 |
| [bounds/route_cg/delivery_pricer.py](bounds/route_cg/delivery_pricer.py) | 纯配送路线的 MILP 定价 |
| [bounds/route_cg/label_bound.py](bounds/route_cg/label_bound.py) | 标签定价的后续路径下界 |
| [bounds/route_cg/pricing_schedule.py](bounds/route_cg/pricing_schedule.py) | 配送定价路径的整数时间可行性 |
| [bounds/route_cg/route_pricing.py](bounds/route_cg/route_pricing.py) | 纯配送路线标签定价 |
| [bounds/route_cg/time_budget.py](bounds/route_cg/time_budget.py) | 物理车队的时间预算约束 |
| [branch_cut/capacity.py](branch_cut/capacity.py) | 单批次容量割 |
| [branch_cut/dual_cuts.py](branch_cut/dual_cuts.py) | 配送定价证书转距离约束 |
| [branch_cut/short_cycles.py](branch_cut/short_cycles.py) | 单批次 MILP 的二节点子环约束 |
| [branch_cut/site_flow.py](branch_cut/site_flow.py) | 自由分仓的仓库流连接 |
| [primal/construction.py](primal/construction.py) | 送取分趟的插入初解 |
| [primal/pool_master.py](primal/pool_master.py) | 路线池整数主问题 |
| [primal/route_pool.py](primal/route_pool.py) | 从受限路线池构造并审计可行解 |
| [primal/schedule_start.py](primal/schedule_start.py) | 最早可行日程和车辆编号规范化 |

公开包调用保持可用。已移除旧内部目录，不保留空目录或转发文件；直接导入内部模块的外部脚本需要更新路径。

参见[目录指南](../../../../docs/solver_directory_guide.md)、[Gurobi 对照说明](../../../../docs/exact_milp_comparison.md)。
