M8 联合分批成本运行结果

包装：循环；批次数：1。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：134.20280317203614；循环箱池：20。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 20 | 20 | 0 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / (60 × time_tick_subdivision)，细分倍率缺省为1。

启用配送批次数：1；总距离：519.7186479112307。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
每个计入批次数的批次都必须有配送和回运；纯回收趟次显式计费。初始箱体投资单列。
截点仅决定本轮回运资格；二级车辆在第二阶段仍可配送和回收。

截点模式：optimized。

| 批次 | 开始/分钟 | 回运截点/分钟 | 第一窗/分钟 | 第二窗/分钟 | 结束/分钟 |
| --- | ---: | ---: | ---: | ---: | ---: |
| b1 | 480 | 1195.88 | 715.883 | 64.1167 | 1260 |

- container_pool: 100
- e1_distance: 15.633741552949255
- e1_work: 0.0
- e1_vehicles: 0
- e1_trips: 0
- e2_distance: 18.16906161908691
- e2_work: 0.0
- e2_vehicles: 0
- e2_trips: 0
- cleaning: 0.2
- tracking: 0
- container_wear: 0.2

批次数允许域：[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]；选定周期批次数：1。
实际空箱回运轮数：1。
日历模式：flexible_cycle；逐批长度/ticks：[46800]；逐批分界比例：{'b1': 0.9177991452991453}。

| K | 状态 | 有效成本 | 箱数 | 该K成本最优已证明 |
| --- | --- | ---: | ---: | --- |
| 1 | FEASIBLE | 134.20280317203614 | 20 | False |
| 2 | FEASIBLE | 158.4325879175014 | 18 | False |
| 3 | FEASIBLE | 194.20419699725124 | 16 | False |
| 4 | HEURISTIC_NO_SOLUTION | None | None | False |
| 5 | HEURISTIC_NO_SOLUTION | None | None | False |
| 6 | HEURISTIC_NO_SOLUTION | None | None | False |
| 7 | HEURISTIC_NO_SOLUTION | None | None | False |
| 8 | HEURISTIC_NO_SOLUTION | None | None | False |
| 9 | HEURISTIC_NO_SOLUTION | None | None | False |
| 10 | HEURISTIC_NO_SOLUTION | None | None | False |
| 11 | HEURISTIC_NO_SOLUTION | None | None | False |
| 12 | HEURISTIC_NO_SOLUTION | None | None | False |
| 13 | HEURISTIC_NO_SOLUTION | None | None | False |
| 14 | HEURISTIC_NO_SOLUTION | None | None | False |
| 15 | HEURISTIC_NO_SOLUTION | None | None | False |
| 16 | HEURISTIC_NO_SOLUTION | None | None | False |
| 17 | HEURISTIC_NO_SOLUTION | None | None | False |
| 18 | HEURISTIC_NO_SOLUTION | None | None | False |
| 19 | HEURISTIC_NO_SOLUTION | None | None | False |
| 20 | HEURISTIC_NO_SOLUTION | None | None | False |
