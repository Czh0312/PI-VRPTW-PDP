# M3 — 精确求解

本目录拥有独立实现，不导入其他模型的求解代码。相似文件不自动同步。

| 文件 | 用途 |
| --- | --- |
| [distance_bounds.py](distance_bounds.py) | 必访节点的保守距离下界 |
| [domains.py](domains.py) | 客户可选仓点与安全时间域 |
| [engine.py](engine.py) | 本模型内部的求解主过程 |
| [formulation.py](formulation.py) | 本模型单批次 MILP 建模分流及输入检查 |
| [initial_solution.py](initial_solution.py) | 确定性可行初解分流 |
| [model.py](model.py) | 该编号的 MILP 建模入口 |
| [pickup_route_pricing.py](pickup_route_pricing.py) | 本模型内部的送取任务路线定价与插入列 |
| [single_batch_columns.py](single_batch_columns.py) | 单批次路线列生成下界 |
| [single_batch_composite_proof.py](single_batch_composite_proof.py) | M3 分量相等的可选最优性证明 |
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
