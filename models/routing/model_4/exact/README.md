# M4 — 精确求解

本目录拥有独立实现，不导入其他模型的求解代码。相似文件不自动同步。

先按 `parameters.py` → `variables.py` → `constraints.py` → `objective.py` 阅读数学定义，
再看组装文件和 `solver.py`。参数类复用公共业务实例，变量类管理同一个 Gurobi 模型。
完整导航见[精确模型结构](../../../../docs/exact_model_layout.md)。

顶层只保留入口与核心定义；`search/` 管理搜索流程，`formulations/` 组装数学模型，`accelerators/` 收纳加速与辅助组件（有需要时才设置子目录）。
日常调用使用本目录的 `solve_exact`；内部模块路径不作为稳定公共接口。
统一阅读方法与迁移说明见[目录指南](../../../../docs/solver_directory_guide.md)。

## 入口与核心定义

| 文件 | 用途 |
| --- | --- |
| [constraints.py](constraints.py) | 按业务含义组织的约束组 |
| [initialization.py](initialization.py) | 确定性可行初解分流 |
| [model.py](model.py) | MILP 建模入口；区分联合分仓与固定分仓子问题 |
| [objective.py](objective.py) | 距离或完整成本目标函数 |
| [parameters.py](parameters.py) | 已知参数、安全派生界及参数数据类 |
| [solver.py](solver.py) | 本模型公开求解入口及编号校验 |
| [variables.py](variables.py) | 决策变量定义与 `ModelVariables` 分组管理 |

## 搜索流程：批次数、分仓与子问题

| 文件 | 用途 |
| --- | --- |
| [search/assignment.py](search/assignment.py) | 联合分仓 MILP 与分仓域树选择 |
| [search/assignment_tree.py](search/assignment_tree.py) | 完整分仓域树及证明汇总 |
| [search/engine.py](search/engine.py) | 本模型内部的求解主过程 |

## 数学模型组装

| 文件 | 用途 |
| --- | --- |
| [formulations/full.py](formulations/full.py) | 本模型单批次 MILP 建模分流及输入检查 |
| [formulations/single_batch.py](formulations/single_batch.py) | 单批次 MILP 的参数、变量、约束及目标组装 |

## 加速与辅助组件

| 文件 | 用途 |
| --- | --- |
| [accelerators/distance_bounds.py](accelerators/distance_bounds.py) | 必访节点的保守距离下界 |
| [accelerators/domains.py](accelerators/domains.py) | 客户可选仓点与安全时间域 |
| [accelerators/pickup_route_pricing.py](accelerators/pickup_route_pricing.py) | 本模型内部的送取任务路线定价与插入列 |
| [accelerators/strengthening.py](accelerators/strengthening.py) | 单批次安全界、稀疏弧和车辆趟数界 |

## 单批次加速组件

| 文件 | 用途 |
| --- | --- |
| [accelerators/single_batch/columns.py](accelerators/single_batch/columns.py) | 单批次路线列生成下界 |
| [accelerators/single_batch/cuts.py](accelerators/single_batch/cuts.py) | 单批次容量割 |
| [accelerators/single_batch/delivery_pricer.py](accelerators/single_batch/delivery_pricer.py) | 纯配送路线的 MILP 定价 |
| [accelerators/single_batch/dual_cuts.py](accelerators/single_batch/dual_cuts.py) | 配送定价证书转距离约束 |
| [accelerators/single_batch/e1_bound.py](accelerators/single_batch/e1_bound.py) | M3 一级路线覆盖松弛下界 |
| [accelerators/single_batch/label_bound.py](accelerators/single_batch/label_bound.py) | 标签定价的后续路径下界 |
| [accelerators/single_batch/physical_bounds.py](accelerators/single_batch/physical_bounds.py) | M3 原物理站点的可选成本下界 |
| [accelerators/single_batch/pickup_bound.py](accelerators/single_batch/pickup_bound.py) | 送取混合任务的路线覆盖下界 |
| [accelerators/single_batch/pool_master.py](accelerators/single_batch/pool_master.py) | 路线池整数主问题 |
| [accelerators/single_batch/pricing_schedule.py](accelerators/single_batch/pricing_schedule.py) | 配送定价路径的整数时间可行性 |
| [accelerators/single_batch/primal.py](accelerators/single_batch/primal.py) | 送取分趟的插入初解 |
| [accelerators/single_batch/route_pool.py](accelerators/single_batch/route_pool.py) | 从受限路线池构造并审计可行解 |
| [accelerators/single_batch/route_pricing.py](accelerators/single_batch/route_pricing.py) | 纯配送路线标签定价 |
| [accelerators/single_batch/schedule_start.py](accelerators/single_batch/schedule_start.py) | 最早可行日程和车辆编号规范化 |
| [accelerators/single_batch/short_cycles.py](accelerators/single_batch/short_cycles.py) | 单批次 MILP 的二节点子环约束 |
| [accelerators/single_batch/site_flow.py](accelerators/single_batch/site_flow.py) | 自由分仓的仓库流连接 |
| [accelerators/single_batch/time_budget.py](accelerators/single_batch/time_budget.py) | 物理车队的时间预算约束 |

`__init__.py` 导出当前算法入口。基础数据与审计的依赖边界见[模型导航](../../README.md)。
