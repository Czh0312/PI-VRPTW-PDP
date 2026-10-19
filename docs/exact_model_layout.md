# 精确模型的参数、变量、约束与目标函数

M1–M8 的精确模型按数学职责组织。每个模型独立拥有下列文件，继续复用
`models/common/` 中的业务实例、订单、运行规则和车队，以及 `models/cost/` 中的成本口径。

| 文件 | 数学职责 | 阅读重点 |
| --- | --- | --- |
| `parameters.py` | 已知参数及安全派生界 | `ModelParameters`、`prepare_parameters`；M5 另有 `MultibatchParameters` |
| `variables.py` | 决策变量定义和管理 | `ModelVariables` 的变量创建方法、路线和任务变量分组 |
| `constraints.py` | 业务约束 | 分仓／分批、路径、容量、时间、车辆串行、回收量、清洁库存 |
| `objective.py` | 目标函数 | M1–M5 的距离表达；M6–M8 的箱池、距离、工时、趟次及车辆费用 |
| `formulation.py` | 实际组装实现 | 使用同一参数对象与变量容器，连接全部约束组 |
| `milp_solver.py` | 直接 Gurobi 基线 | 显式配置、完整问题范围和原始求解器证据 |
| `solution.py` | 业务解提取 | 路线、分仓、分批、箱池及日历，不聚合跨子问题证明 |
| `model.py` | 公开建模入口 | 模型编号校验，以及完整模型或固定 K 子问题的选择 |
| `solver.py`、`enhanced_solver.py` 等 | 精确求解流程 | 时间预算、初解、Gurobi 调用、结果提取、独立审计和证明汇总 |
| `preprocessing/`、`branch_cut/`、`bounds/` | 模型增强 | 安全界、删弧、有效不等式、可选上下界证书 |

## 模型组装位置

| 模型 | 完整模型组装文件 |
| --- | --- |
| M1 | [formulation.py](../models/routing/model_1/exact/formulation.py) |
| M2 | [formulation.py](../models/routing/model_2/exact/formulation.py) |
| M3 | [formulation.py](../models/routing/model_3/exact/formulation.py) |
| M4 | [formulation.py](../models/routing/model_4/exact/formulation.py) |
| M5 | [formulation.py](../models/routing/model_5/exact/formulation.py)；B=1 专门化在本模型的 `diagnostics/single_batch/formulation.py` |
| M6 | [formulation.py](../models/routing/model_6/exact/formulation.py) |
| M7 | [formulation.py](../models/routing/model_7/exact/formulation.py) |
| M8 | [formulation.py](../models/routing/model_8/exact/formulation.py) |

组装过程准备参数、创建一个 Gurobi 模型与 `ModelVariables`，按业务顺序调用约束组，
最后设置目标函数。主流程的变量创建表达位于 `variables.py` 内，辅助变量按其所需约束分阶段创建，
所有主模型约束组都写入同一个 Gurobi 模型。可选仓库流诊断及上下界子模型继续由
各自增强模块维护。这样保留原来的变量与约束构造顺序。

## 约束组导航

| 约束组 | 对应业务含义 |
| --- | --- |
| `add_assignment_constraints` | M1–M5 的顾客分仓、仓点启用和一级供货容量 |
| `add_calendar_constraints` | M6–M8 的批次边界与分界时刻 |
| `add_order_batch_constraints` | M6–M8 的订单分批、批次启用及订单释放时刻 |
| `add_supply_time_constraints` | 一级供货完成时刻及其安全上下界 |
| `add_route_constraints` | 路径流平衡、访问启用、载荷、时间窗和弧传播 |
| `add_first_echelon_constraints` | 一级装货、配送与回运、同车连续作业 |
| `add_supply_coverage_constraints` | 一级向各前置仓的配送覆盖与数量平衡 |
| `add_cutoff_quantity_constraints` | 多批次回收截止归属与本轮应回运量 |
| `add_second_echelon_constraints` | 二级趟次、供货衔接、回收与物理车辆日程 |
| `add_shared_route_constraints` | M1–M5 的共享弧、路线归属、时间、载荷及因果序位连接 |
| `add_task_and_return_coverage_constraints` | 任务唯一覆盖、顾客使用时长及一级空箱回运平衡 |
| `add_inventory_constraints` | 每次领箱时清洁库存充足，且只计已卸回并清洗完成的箱量 |
| `add_vehicle_activation_constraints` | M6–M8 的物理车辆启用与其全部趟次关联 |

M5 的多批次函数使用 `multibatch_` 前缀，与其 B=1 专门化区分。模型增强和容量割仍由
各模型原有增强模块负责，组装入口保留其启用条件。

## 查看参数和变量

公开 `build_model` 的参数与返回形式继续兼容原有调用。返回字典新增两个对象：

```python
from models.routing.model_3.exact.model import build_model

model, context = build_model(problem)
try:
    parameters = context["parameters"]
    variables = context["variables"]
    print(parameters.policy)           # 批次、截止、清洗和周转等已知规则
    print(parameters.fleet)            # 车队与容量
    print(parameters.task_windows)     # 安全派生时间界
    print(variables.task_times)         # 配送／回收决策时刻
    print(variables.routes["e2"])      # 二级路线的访问、弧与时刻变量
finally:
    model.dispose()
```

`ModelParameters` 是冻结的数据类，其业务输入直接引用公共实例；它不包含 Gurobi 决策变量。
`ModelVariables` 管理同一模型的变量及线性表达分组，辅助变量也由它的创建方法定义。
`context` 中原有的 `e1`、`e2`、`assignments` 等字段继续可用。

这次整理调整的是代码组织。M2/M4 的联合分仓或完整分仓域树、M6–M8 的 K 搜索、
整数时间刻度、车队趟位上限、成本定义及全局最优性判定都继续沿用原有流程。

## 公式到实现的维护方式

每个约束组的函数名作为稳定锚点。已有论文若尚未冻结公式编号，不在代码中虚构编号；
在论文确定后将实际编号补充到下表和对应函数说明中。

| 数学含义 | M1–M4 实现 | M5 多批次实现 | 关键区别 |
| --- | --- | --- | --- |
| 顾客分仓与仓点启用 | `add_assignment_constraints` | `batching.multibatch_add_assignment_constraints` | M2/M4 决策自由分仓，其他模型固定 |
| 路径、容量与时间传播 | `add_route_constraints` | `routing.multibatch_add_route_constraints` | 共享变量和路线副本仍属于同一主模型 |
| 两级供货及车辆串行 | `add_first_echelon_constraints` / `add_second_echelon_constraints` | `echelon` 对应函数 | 保留跨级时间与同车连续作业 |
| 任务覆盖与空箱回运 | `add_task_and_return_coverage_constraints` | `recovery` 对应函数 | 一次性包装不激活回收任务 |
| 清洁库存 | `add_inventory_constraints` | `inventory.multibatch_add_inventory_constraints` | 多批次只计已回运且清洗完成的箱量 |
| 外部距离下界行 | 可选证书模块 | `strengthening.multibatch_add_distance_certificate_constraints` | 属于可选强化，不代替库存或任务覆盖 |
| M6–M8 日历与订单分批 | 各模型 `add_calendar_constraints` / `add_order_batch_constraints` | 不适用 | 共同分界、独立分界及可变批长由本模型参数和变量表达 |

Gurobi 基线仍使用这些数学定义，具体保留与关闭的强化选项见
[Gurobi 对照说明](exact_milp_comparison.md)。`parameters.py` 不保存时间预算、线程或实验策略；
这些属于求解配置。`ModelVariables` 与 context 的已有字段保持兼容，提取模块和初解编码共享这些句柄。
