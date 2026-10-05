M8 联合分批成本运行结果

包装：循环；批次数：2。
状态：OPTIMAL。
独立审计：True；已证明最优：True。
总成本：80.00000000000001；循环箱池：1。
已证明不可行：False；证明范围：model_8_all_configured_counts_integer_time_cost_MILP。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 1 | 1 | 0 |
| b2 | 1 | 1 | 0 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：2；总距离：26.0。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留回收义务；纯回收趟次显式计费。初始箱体投资单列。
截点仅决定本轮回运资格；二级车辆在第二阶段仍可配送和回收。

截点模式：optimized。

| 批次 | 开始/分钟 | 回运截点/分钟 | 第一窗/分钟 | 第二窗/分钟 | 结束/分钟 |
| --- | ---: | ---: | ---: | ---: | ---: |
| b1 | 0 | 9 | 9 | 61 | 70 |
| b2 | 70 | 81 | 11 | 49 | 130 |

- container_pool: 30
- e1_distance: 20.0
- e1_work: 3.0
- e1_vehicles: 10
- e1_trips: 2
- e2_distance: 6.0
- e2_work: 1.0
- e2_vehicles: 5
- e2_trips: 2
- cleaning: 0.4
- tracking: 0.2
- container_wear: 0.4

日历模式：flexible_cycle；K 搜索范围：[1, 2]。
逐批长度/ticks：[70, 60]；逐批分界比例：{'b1': 0.12857142857142856, 'b2': 0.18333333333333332}。
实际空箱回运轮数：2。
所有配置K均证明最优或不可行，才能声明整个模型成本最优。

| K | 状态 | 有效成本 | 箱数 | 该K成本最优已证明 |
| --- | --- | ---: | ---: | --- |
| 1 | INFEASIBLE | None | None | False |
| 2 | OPTIMAL | 80.00000000000001 | 1 | True |
