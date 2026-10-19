# M5 精确求解代码导航

从 `solver.py` 开始：`strategy="milp"` 调用 `milp_solver.py`，默认 `auto` 使用本模型定制流程。
两条路径共用 `formulation.py` 的数学定义和 `solution.py` 的业务解提取。

## 职责边界

- `preprocessing/`：安全时间界、删弧与分仓域。
- `bounds/`：下界、松弛和定价证书。松弛最优不等于原问题最优。
- `branch_cut/`：完整 MILP 的有效不等式及容量割；不是独立的完整分支定价算法。
- `primal/`：构造或改进可行上界，进入求解结果前需独立审计。
- `constraint_blocks/`：按业务拆分完整模型约束。
- `diagnostics/single_batch/`：B=1 专门化建模及辅助组件；B=1 调用仍可使用，不作为一般多批次模型的替代。
- [`../hybrid/solver.py`](../hybrid/solver.py)：显式 ALNS + 精确组件混合流程；只有主动调用才加载。

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
| [constraint_blocks/batching.py](constraint_blocks/batching.py) | 多批分仓、批次与一级供货覆盖 |
| [constraint_blocks/echelon.py](constraint_blocks/echelon.py) | 两级供货衔接与物理车辆串行 |
| [constraint_blocks/inventory.py](constraint_blocks/inventory.py) | 回运清洗与清洁库存 |
| [constraint_blocks/recovery.py](constraint_blocks/recovery.py) | 截止回收归属与任务覆盖 |
| [constraint_blocks/routing.py](constraint_blocks/routing.py) | 路径流、载荷与时间传播 |
| [constraint_blocks/single_batch.py](constraint_blocks/single_batch.py) | 单批次业务约束组 |
| [constraint_blocks/strengthening.py](constraint_blocks/strengthening.py) | 安全强化与外部距离证书行 |
| [bounds/conditional_physical/group_cuts.py](bounds/conditional_physical/group_cuts.py) | 任务组条件约束 |
| [bounds/conditional_physical/group_schedule.py](bounds/conditional_physical/group_schedule.py) | 固定任务组的物理排程 |
| [bounds/conditional_physical/logic_benders.py](bounds/conditional_physical/logic_benders.py) | 可续跑的条件物理站点分离 |
| [bounds/conditional_physical/physical_ceiling.py](bounds/conditional_physical/physical_ceiling.py) | 目标上限可行性检查得到的条件下界 |
| [bounds/conditional_physical/physical_cuts.py](bounds/conditional_physical/physical_cuts.py) | 联合松弛中的条件物理成本割 |
| [bounds/conditional_physical/physical_site.py](bounds/conditional_physical/physical_site.py) | 保留物理车队日历的站点条件下界 |
| [bounds/distance.py](bounds/distance.py) | 必访节点的保守距离下界 |
| [bounds/distance_certificate.py](bounds/distance_certificate.py) | M5 距离下界证书 |
| [bounds/inventory/conditional_inventory.py](bounds/inventory/conditional_inventory.py) | 供货条件下的二级成本界 |
| [bounds/inventory/e1_inventory.py](bounds/inventory/e1_inventory.py) | 一级路线与库存松弛下界 |
| [bounds/inventory/e1_routes.py](bounds/inventory/e1_routes.py) | 小规模一级路线完整枚举 |
| [bounds/inventory/inventory_guard.py](bounds/inventory/inventory_guard.py) | 库存松弛下界的独立核验 |
| [bounds/inventory/joint_inventory.py](bounds/inventory/joint_inventory.py) | 完整二级覆盖与一级供货／清洁库存联合松弛 |
| [bounds/route_cg/pickup_pricing.py](bounds/route_cg/pickup_pricing.py) | 本模型内部的送取任务路线定价与插入列 |
| [bounds/route_cover_cg/capacity_cuts.py](bounds/route_cover_cg/capacity_cuts.py) | 配送容量取整割 |
| [bounds/route_cover_cg/capacity_dual_repair.py](bounds/route_cover_cg/capacity_dual_repair.py) | 容量、计数、任务价格的完整域对偶修复 |
| [bounds/route_cover_cg/count_dual_repair.py](bounds/route_cover_cg/count_dual_repair.py) | 配送计数约束的对偶修复 |
| [bounds/route_cover_cg/pricing_polish.py](bounds/route_cover_cg/pricing_polish.py) | 细化现有完整域对偶证书 |
| [bounds/route_cover_cg/route_cg.py](bounds/route_cover_cg/route_cg.py) | 完整域路线覆盖列生成下界 |
| [bounds/route_cover_cg/route_enumeration.py](bounds/route_cover_cg/route_enumeration.py) | 小规模二级基本路线完整枚举 |
| [bounds/route_cover_cg/route_integer_cover.py](bounds/route_cover_cg/route_integer_cover.py) | 完整路线域上的整数覆盖下界 |
| [bounds/route_cover_cg/route_timing.py](bounds/route_cover_cg/route_timing.py) | 二级成本与最迟发车的完整前沿 |
| [bounds/route_cover_cg/subset_rows.py](bounds/route_cover_cg/subset_rows.py) | 完整带时刻路线域的子集约束 |
| [bounds/route_cover_cg/task_pairs.py](bounds/route_cover_cg/task_pairs.py) | 任务同路线的整数性增强 |
| [branch_cut/capacity.py](branch_cut/capacity.py) | 完整送取任务对子集容量割 |
| [primal/construction.py](primal/construction.py) | 独立于 ALNS 的 M5 确定性初解 |
| [primal/cover_pool.py](primal/cover_pool.py) | 保持完整任务覆盖的可行解路线池 |
| [primal/cover_schedule.py](primal/cover_schedule.py) | 固定完整二级路径后的物理车辆排程 |
| [primal/e1_polish.py](primal/e1_polish.py) | 固定二级序列后改进一级路线 |
| [primal/hybrid_primal.py](primal/hybrid_primal.py) | 同次求解内收集可行路线候选 |
| [primal/route_pool.py](primal/route_pool.py) | 受限路线池上的可行解改进 |
| [primal/solve_with_improvement.py](primal/solve_with_improvement.py) | 可选一级改进及路线池改进的预算编排 |
| [primal/vehicle_retime.py](primal/vehicle_retime.py) | 固定路径重分配物理车辆并重新排程 |
| [diagnostics/single_batch/bounds/e1.py](diagnostics/single_batch/bounds/e1.py) | B=1 一级路线覆盖松弛下界 |
| [diagnostics/single_batch/bounds/physical.py](diagnostics/single_batch/bounds/physical.py) | B=1 物理站点的可选成本下界 |
| [diagnostics/single_batch/bounds/pickup.py](diagnostics/single_batch/bounds/pickup.py) | 送取混合任务的路线覆盖下界 |
| [diagnostics/single_batch/bounds/route_cg/columns.py](diagnostics/single_batch/bounds/route_cg/columns.py) | 单批次路线列生成下界 |
| [diagnostics/single_batch/bounds/route_cg/delivery_pricer.py](diagnostics/single_batch/bounds/route_cg/delivery_pricer.py) | 纯配送路线的 MILP 定价 |
| [diagnostics/single_batch/bounds/route_cg/label_bound.py](diagnostics/single_batch/bounds/route_cg/label_bound.py) | 标签定价的后续路径下界 |
| [diagnostics/single_batch/bounds/route_cg/pricing_schedule.py](diagnostics/single_batch/bounds/route_cg/pricing_schedule.py) | 配送定价路径的整数时间可行性 |
| [diagnostics/single_batch/bounds/route_cg/route_pricing.py](diagnostics/single_batch/bounds/route_cg/route_pricing.py) | 纯配送路线标签定价 |
| [diagnostics/single_batch/bounds/route_cg/time_budget.py](diagnostics/single_batch/bounds/route_cg/time_budget.py) | 物理车队的时间预算约束 |
| [diagnostics/single_batch/branch_cut/capacity.py](diagnostics/single_batch/branch_cut/capacity.py) | 单批次容量割 |
| [diagnostics/single_batch/branch_cut/dual_cuts.py](diagnostics/single_batch/branch_cut/dual_cuts.py) | 配送定价证书转距离约束 |
| [diagnostics/single_batch/branch_cut/short_cycles.py](diagnostics/single_batch/branch_cut/short_cycles.py) | 单批次 MILP 的二节点子环约束 |
| [diagnostics/single_batch/branch_cut/site_flow.py](diagnostics/single_batch/branch_cut/site_flow.py) | 自由分仓的仓库流连接 |
| [diagnostics/single_batch/formulation.py](diagnostics/single_batch/formulation.py) | 单批次 MILP 的参数、变量、约束及目标组装 |
| [diagnostics/single_batch/primal/construction.py](diagnostics/single_batch/primal/construction.py) | 送取分趟的插入初解 |
| [diagnostics/single_batch/primal/pool_master.py](diagnostics/single_batch/primal/pool_master.py) | 路线池整数主问题 |
| [diagnostics/single_batch/primal/route_pool.py](diagnostics/single_batch/primal/route_pool.py) | 从受限路线池构造并审计可行解 |
| [diagnostics/single_batch/primal/schedule_start.py](diagnostics/single_batch/primal/schedule_start.py) | 最早可行日程和车辆编号规范化 |

`bounds/__init__.py` 提供带 M5 编号校验的证书接口；`primal/__init__.py` 提供带编号校验的改进接口和显式混合入口。

公开包调用保持可用。已移除旧内部目录，不保留空目录或转发文件；直接导入内部模块的外部脚本需要更新路径。

参见[目录指南](../../../../docs/solver_directory_guide.md)、[Gurobi 对照说明](../../../../docs/exact_milp_comparison.md)。
