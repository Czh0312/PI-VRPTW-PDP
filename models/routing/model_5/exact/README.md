# M5 — 精确求解

本目录拥有独立实现，不导入其他模型的求解代码。相似文件不自动同步。

| 文件 | 用途 |
| --- | --- |
| [bounds.py](bounds.py) | 有编号校验的下界证书入口 |
| [distance_bounds.py](distance_bounds.py) | 必访节点的保守距离下界 |
| [domains.py](domains.py) | 客户可选仓点与安全时间域 |
| [engine.py](engine.py) | 本模型内部的求解主过程 |
| [formulation.py](formulation.py) | 建模分流及多批次车辆索引 MILP |
| [initial_solution.py](initial_solution.py) | 确定性可行初解分流 |
| [model.py](model.py) | 有编号校验的 MILP 建模入口 |
| [multibatch_bounds.py](multibatch_bounds.py) | M5 距离下界证书 |
| [multibatch_capacity_cuts.py](multibatch_capacity_cuts.py) | 配送容量取整割 |
| [multibatch_capacity_dual_repair.py](multibatch_capacity_dual_repair.py) | 容量、计数、任务价格的完整域对偶修复 |
| [multibatch_conditional_inventory.py](multibatch_conditional_inventory.py) | 供货条件下的二级成本界 |
| [multibatch_count_dual_repair.py](multibatch_count_dual_repair.py) | 配送计数约束的对偶修复 |
| [multibatch_cover_pool.py](multibatch_cover_pool.py) | 保持完整任务覆盖的可行解路线池 |
| [multibatch_cover_schedule.py](multibatch_cover_schedule.py) | 固定完整二级路径后的物理车辆排程 |
| [multibatch_cuts.py](multibatch_cuts.py) | 完整送取任务对子集容量割 |
| [multibatch_e1_inventory.py](multibatch_e1_inventory.py) | 一级路线与库存松弛下界 |
| [multibatch_e1_polish.py](multibatch_e1_polish.py) | 固定二级序列后改进一级路线 |
| [multibatch_e1_routes.py](multibatch_e1_routes.py) | 小规模一级路线完整枚举 |
| [multibatch_group_cuts.py](multibatch_group_cuts.py) | 任务组条件约束 |
| [multibatch_group_schedule.py](multibatch_group_schedule.py) | 固定任务组的物理排程 |
| [multibatch_hybrid.py](multibatch_hybrid.py) | 显式混合实验入口：初始搜索、路线池改进和下界证书 |
| [multibatch_hybrid_primal.py](multibatch_hybrid_primal.py) | 同次求解内收集可行路线候选 |
| [multibatch_inventory_guard.py](multibatch_inventory_guard.py) | 库存松弛下界的独立核验 |
| [multibatch_joint_inventory.py](multibatch_joint_inventory.py) | 完整二级覆盖与一级供货／清洁库存联合松弛 |
| [multibatch_logic_benders.py](multibatch_logic_benders.py) | 可续跑的条件物理站点分离 |
| [multibatch_physical_ceiling.py](multibatch_physical_ceiling.py) | 目标上限可行性检查得到的条件下界 |
| [multibatch_physical_cuts.py](multibatch_physical_cuts.py) | 联合松弛中的条件物理成本割 |
| [multibatch_physical_site.py](multibatch_physical_site.py) | 保留物理车队日历的站点条件下界 |
| [multibatch_pricing_polish.py](multibatch_pricing_polish.py) | 细化现有完整域对偶证书 |
| [multibatch_primal.py](multibatch_primal.py) | 独立于 ALNS 的 M5 确定性初解 |
| [multibatch_route_cg.py](multibatch_route_cg.py) | 完整域路线覆盖列生成下界 |
| [multibatch_route_enumeration.py](multibatch_route_enumeration.py) | 小规模二级基本路线完整枚举 |
| [multibatch_route_integer_cover.py](multibatch_route_integer_cover.py) | 完整路线域上的整数覆盖下界 |
| [multibatch_route_pool.py](multibatch_route_pool.py) | 受限路线池上的可行解改进 |
| [multibatch_route_timing.py](multibatch_route_timing.py) | 二级成本与最迟发车的完整前沿 |
| [multibatch_subset_rows.py](multibatch_subset_rows.py) | 完整带时刻路线域的子集约束 |
| [multibatch_task_pairs.py](multibatch_task_pairs.py) | 任务同路线的整数性增强 |
| [multibatch_vehicle_retime.py](multibatch_vehicle_retime.py) | 固定路径重分配物理车辆并重新排程 |
| [pickup_route_pricing.py](pickup_route_pricing.py) | 本模型内部的送取任务路线定价与插入列 |
| [primal.py](primal.py) | 有编号校验的可行解改进与混合求解入口 |
| [single_batch_columns.py](single_batch_columns.py) | 单批次路线列生成下界 |
| [single_batch_cuts.py](single_batch_cuts.py) | 单批次容量割 |
| [single_batch_delivery_pricer.py](single_batch_delivery_pricer.py) | 纯配送路线的 MILP 定价 |
| [single_batch_dual_cuts.py](single_batch_dual_cuts.py) | 配送定价证书转距离约束 |
| [single_batch_e1_bound.py](single_batch_e1_bound.py) | M3 一级路线覆盖松弛下界 |
| [single_batch_formulation.py](single_batch_formulation.py) | 单批次 MILP 的本模型内部建模 |
| [single_batch_label_bound.py](single_batch_label_bound.py) | 标签定价的后续路径下界 |
| [single_batch_physical_bounds.py](single_batch_physical_bounds.py) | M3 原物理站点的可选成本下界 |
| [single_batch_pickup_bound.py](single_batch_pickup_bound.py) | 送取混合任务的路线覆盖下界 |
| [single_batch_pool_master.py](single_batch_pool_master.py) | 路线池整数主问题 |
| [single_batch_pricing_schedule.py](single_batch_pricing_schedule.py) | 配送定价路径的整数时间可行性 |
| [single_batch_primal.py](single_batch_primal.py) | 送取分趟的插入初解 |
| [single_batch_route_pool.py](single_batch_route_pool.py) | 从受限路线池构造并审计可行解 |
| [single_batch_route_pricing.py](single_batch_route_pricing.py) | 纯配送路线标签定价 |
| [single_batch_schedule_start.py](single_batch_schedule_start.py) | 最早可行日程和车辆编号规范化 |
| [single_batch_short_cycles.py](single_batch_short_cycles.py) | 单批次 MILP 的二节点子环约束 |
| [single_batch_site_flow.py](single_batch_site_flow.py) | 自由分仓的仓库流连接 |
| [single_batch_time_budget.py](single_batch_time_budget.py) | 物理车队的时间预算约束 |
| [solver.py](solver.py) | 本模型公开求解入口及编号校验 |
| [strengthening.py](strengthening.py) | 单批次安全界、稀疏弧和车辆趟数界 |

`__init__.py` 导出当前算法入口。基础数据与审计的依赖边界见[模型导航](../../README.md)。
