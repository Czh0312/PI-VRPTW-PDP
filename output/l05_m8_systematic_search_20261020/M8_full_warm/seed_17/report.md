M8 联合分批成本运行结果

包装：循环；批次数：3。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：382.68404098476674；循环箱池：35。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 35 | 35 | 0 |
| b2 | 35 | 35 | 0 |
| b3 | 30 | 30 | 0 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / (60 × time_tick_subdivision)，细分倍率缺省为1。

启用配送批次数：3；总距离：2992.819907582137。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
每个计入批次数的批次都必须有配送和回运；纯回收趟次显式计费。初始箱体投资单列。
截点仅决定本轮回运资格；二级车辆在第二阶段仍可配送和回收。

截点模式：optimized。

| 批次 | 开始/分钟 | 回运截点/分钟 | 第一窗/分钟 | 第二窗/分钟 | 结束/分钟 |
| --- | ---: | ---: | ---: | ---: | ---: |
| b1 | 480 | 660 | 180 | 93.6 | 753.6 |
| b2 | 753.6 | 920 | 166.4 | 94.4 | 1014.4 |
| b3 | 1014.4 | 1180 | 165.6 | 80 | 1260 |

- container_pool: 175
- e1_distance: 112.08609121131973
- e1_work: 0.0
- e1_vehicles: 0
- e1_trips: 0
- e2_distance: 93.597949773447
- e2_work: 0.0
- e2_vehicles: 0
- e2_trips: 0
- cleaning: 1.0
- tracking: 0
- container_wear: 1.0

批次数允许域：[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100]；选定周期批次数：3。
实际空箱回运轮数：3。
日历模式：flexible_cycle；逐批长度/ticks：[16416, 15648, 14736]；逐批分界比例：{'b1': 0.6578947368421053, 'b2': 0.6380368098159509, 'b3': 0.6742671009771987}。

| K | 状态 | 有效成本 | 箱数 | 该K成本最优已证明 |
| --- | --- | ---: | ---: | --- |
| 1 | FEASIBLE | 658.6563833649623 | 100 | False |
| 2 | FEASIBLE | 546.8759893125322 | 65 | False |
| 3 | FEASIBLE | 382.68404098476674 | 35 | False |
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
| 21 | HEURISTIC_NO_SOLUTION | None | None | False |
| 22 | HEURISTIC_NO_SOLUTION | None | None | False |
| 23 | HEURISTIC_NO_SOLUTION | None | None | False |
| 24 | HEURISTIC_NO_SOLUTION | None | None | False |
| 25 | HEURISTIC_NO_SOLUTION | None | None | False |
| 26 | HEURISTIC_NO_SOLUTION | None | None | False |
| 27 | HEURISTIC_NO_SOLUTION | None | None | False |
| 28 | HEURISTIC_NO_SOLUTION | None | None | False |
| 29 | HEURISTIC_NO_SOLUTION | None | None | False |
| 30 | HEURISTIC_NO_SOLUTION | None | None | False |
| 31 | HEURISTIC_NO_SOLUTION | None | None | False |
| 32 | HEURISTIC_NO_SOLUTION | None | None | False |
| 33 | HEURISTIC_NO_SOLUTION | None | None | False |
| 34 | HEURISTIC_NO_SOLUTION | None | None | False |
| 35 | HEURISTIC_NO_SOLUTION | None | None | False |
| 36 | HEURISTIC_NO_SOLUTION | None | None | False |
| 37 | HEURISTIC_NO_SOLUTION | None | None | False |
| 38 | HEURISTIC_NO_SOLUTION | None | None | False |
| 39 | HEURISTIC_NO_SOLUTION | None | None | False |
| 40 | HEURISTIC_NO_SOLUTION | None | None | False |
| 41 | HEURISTIC_NO_SOLUTION | None | None | False |
| 42 | HEURISTIC_NO_SOLUTION | None | None | False |
| 43 | HEURISTIC_NO_SOLUTION | None | None | False |
| 44 | HEURISTIC_NO_SOLUTION | None | None | False |
| 45 | HEURISTIC_NO_SOLUTION | None | None | False |
| 46 | HEURISTIC_NO_SOLUTION | None | None | False |
| 47 | HEURISTIC_NO_SOLUTION | None | None | False |
| 48 | HEURISTIC_NO_SOLUTION | None | None | False |
| 49 | HEURISTIC_NO_SOLUTION | None | None | False |
| 50 | HEURISTIC_NO_SOLUTION | None | None | False |
| 51 | HEURISTIC_NO_SOLUTION | None | None | False |
| 52 | HEURISTIC_NO_SOLUTION | None | None | False |
| 53 | HEURISTIC_NO_SOLUTION | None | None | False |
| 54 | HEURISTIC_NO_SOLUTION | None | None | False |
| 55 | HEURISTIC_NO_SOLUTION | None | None | False |
| 56 | HEURISTIC_NO_SOLUTION | None | None | False |
| 57 | HEURISTIC_NO_SOLUTION | None | None | False |
| 58 | HEURISTIC_NO_SOLUTION | None | None | False |
| 59 | HEURISTIC_NO_SOLUTION | None | None | False |
| 60 | HEURISTIC_NO_SOLUTION | None | None | False |
| 61 | HEURISTIC_NO_SOLUTION | None | None | False |
| 62 | HEURISTIC_NO_SOLUTION | None | None | False |
| 63 | HEURISTIC_NO_SOLUTION | None | None | False |
| 64 | HEURISTIC_NO_SOLUTION | None | None | False |
| 65 | HEURISTIC_NO_SOLUTION | None | None | False |
| 66 | HEURISTIC_NO_SOLUTION | None | None | False |
| 67 | HEURISTIC_NO_SOLUTION | None | None | False |
| 68 | HEURISTIC_NO_SOLUTION | None | None | False |
| 69 | HEURISTIC_NO_SOLUTION | None | None | False |
| 70 | HEURISTIC_NO_SOLUTION | None | None | False |
| 71 | HEURISTIC_NO_SOLUTION | None | None | False |
| 72 | HEURISTIC_NO_SOLUTION | None | None | False |
| 73 | HEURISTIC_NO_SOLUTION | None | None | False |
| 74 | HEURISTIC_NO_SOLUTION | None | None | False |
| 75 | HEURISTIC_NO_SOLUTION | None | None | False |
| 76 | HEURISTIC_NO_SOLUTION | None | None | False |
| 77 | HEURISTIC_NO_SOLUTION | None | None | False |
| 78 | HEURISTIC_NO_SOLUTION | None | None | False |
| 79 | HEURISTIC_NO_SOLUTION | None | None | False |
| 80 | HEURISTIC_NO_SOLUTION | None | None | False |
| 81 | HEURISTIC_NO_SOLUTION | None | None | False |
| 82 | HEURISTIC_NO_SOLUTION | None | None | False |
| 83 | HEURISTIC_NO_SOLUTION | None | None | False |
| 84 | HEURISTIC_NO_SOLUTION | None | None | False |
| 85 | HEURISTIC_NO_SOLUTION | None | None | False |
| 86 | HEURISTIC_NO_SOLUTION | None | None | False |
| 87 | HEURISTIC_NO_SOLUTION | None | None | False |
| 88 | HEURISTIC_NO_SOLUTION | None | None | False |
| 89 | HEURISTIC_NO_SOLUTION | None | None | False |
| 90 | HEURISTIC_NO_SOLUTION | None | None | False |
| 91 | HEURISTIC_NO_SOLUTION | None | None | False |
| 92 | HEURISTIC_NO_SOLUTION | None | None | False |
| 93 | HEURISTIC_NO_SOLUTION | None | None | False |
| 94 | HEURISTIC_NO_SOLUTION | None | None | False |
| 95 | HEURISTIC_NO_SOLUTION | None | None | False |
| 96 | HEURISTIC_NO_SOLUTION | None | None | False |
| 97 | HEURISTIC_NO_SOLUTION | None | None | False |
| 98 | HEURISTIC_NO_SOLUTION | None | None | False |
| 99 | HEURISTIC_NO_SOLUTION | None | None | False |
| 100 | HEURISTIC_NO_SOLUTION | None | None | False |
