M9 联合分批成本运行结果

包装：循环；批次数：2。
状态：OPTIMAL。
独立审计：True；已证明最优：True。
总成本：37.0；循环箱池：1。
已证明不可行：False；证明范围：full_model_9_all_K_joint_cost_integer_time_MILP。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 1 | 1 | 0 |
| b2 | 1 | 1 | 0 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / (60 × time_tick_subdivision)，细分倍率缺省为1。
自由分仓按顾客选择，每个顾客的全部订单保持同仓。完整模型的全局证明状态以 info 为准。

| 顾客 | 选定前置仓 |
| --- | --- |
| C1 | S1 |
| C2 | S2 |

启用配送批次数：2；总距离：24.0。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
每个计入批次数的批次都必须有配送和回运；纯回收趟次显式计费。初始箱体投资单列。
截点仅决定本轮回运资格；二级车辆在第二阶段仍可配送和回收。

截点模式：optimized。

| 批次 | 开始/分钟 | 回运截点/分钟 | 第一窗/分钟 | 第二窗/分钟 | 结束/分钟 |
| --- | ---: | ---: | ---: | ---: | ---: |
| b1 | 0 | 25 | 25 | 15 | 40 |
| b2 | 40 | 75 | 35 | 15 | 90 |

- container_pool: 30
- e1_distance: 2.0
- e1_work: 0.0
- e1_vehicles: 0
- e1_trips: 0
- e2_distance: 4.0
- e2_work: 0.0
- e2_vehicles: 0
- e2_trips: 0
- cleaning: 0.4
- tracking: 0.2
- container_wear: 0.4

批次数允许域：[1, 2, 3]；选定周期批次数：2。
实际空箱回运轮数：2。
日历模式：flexible_cycle；逐批长度/ticks：[40, 50]；逐批分界比例：{'b1': 0.625, 'b2': 0.7}。
批次数、路线、箱池和日历在一个 MILP 中联合优化；一次 Gurobi 求解。
完整候选域已建模：True；全局最优已证明：True；全局下界：37.0。
