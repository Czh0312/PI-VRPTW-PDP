M8 联合分批成本运行结果

包装：循环；批次数：2。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：616.5464711583132；循环箱池：74。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 74 | 72 | 2 |
| b2 | 28 | 28 | 0 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / (60 × time_tick_subdivision)，细分倍率缺省为1。

启用配送批次数：2；总距离：3701.3009133202513。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
每个计入批次数的批次都必须有配送和回运；纯回收趟次显式计费。初始箱体投资单列。
截点仅决定本轮回运资格；二级车辆在第二阶段仍可配送和回收。

截点模式：optimized。

| 批次 | 开始/分钟 | 回运截点/分钟 | 第一窗/分钟 | 第二窗/分钟 | 结束/分钟 |
| --- | ---: | ---: | ---: | ---: | ---: |
| b1 | 480 | 810.35 | 330.35 | 90.7333 | 901.083 |
| b2 | 901.083 | 1153.95 | 252.867 | 106.05 | 1260 |

- container_pool: 370
- e1_distance: 118.96285098460129
- e1_work: 0.0
- e1_vehicles: 0
- e1_trips: 0
- e2_distance: 125.58362017371195
- e2_work: 0.0
- e2_vehicles: 0
- e2_trips: 0
- cleaning: 1.0
- tracking: 0
- container_wear: 1.0

批次数允许域：[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100]；选定周期批次数：2。
实际空箱回运轮数：2。
日历模式：flexible_cycle；逐批长度/ticks：[25265, 21535]；逐批分界比例：{'b1': 0.7845240451217099, 'b2': 0.7045275133503599}。

| K | 状态 | 有效成本 | 箱数 | 该K成本最优已证明 |
| --- | --- | ---: | ---: | --- |
| 1 | FEASIBLE | 663.2533375888761 | 100 | False |
| 2 | FEASIBLE | 616.5464711583132 | 74 | False |
| 3 | FEASIBLE | 705.1129954398914 | 80 | False |
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
| 38 | NOT_RUN_TIME_LIMIT | None | None | False |
| 39 | NOT_RUN_TIME_LIMIT | None | None | False |
| 40 | NOT_RUN_TIME_LIMIT | None | None | False |
| 41 | NOT_RUN_TIME_LIMIT | None | None | False |
| 42 | NOT_RUN_TIME_LIMIT | None | None | False |
| 43 | NOT_RUN_TIME_LIMIT | None | None | False |
| 44 | NOT_RUN_TIME_LIMIT | None | None | False |
| 45 | NOT_RUN_TIME_LIMIT | None | None | False |
| 46 | NOT_RUN_TIME_LIMIT | None | None | False |
| 47 | NOT_RUN_TIME_LIMIT | None | None | False |
| 48 | NOT_RUN_TIME_LIMIT | None | None | False |
| 49 | NOT_RUN_TIME_LIMIT | None | None | False |
| 50 | NOT_RUN_TIME_LIMIT | None | None | False |
| 51 | NOT_RUN_TIME_LIMIT | None | None | False |
| 52 | NOT_RUN_TIME_LIMIT | None | None | False |
| 53 | NOT_RUN_TIME_LIMIT | None | None | False |
| 54 | NOT_RUN_TIME_LIMIT | None | None | False |
| 55 | NOT_RUN_TIME_LIMIT | None | None | False |
| 56 | NOT_RUN_TIME_LIMIT | None | None | False |
| 57 | NOT_RUN_TIME_LIMIT | None | None | False |
| 58 | NOT_RUN_TIME_LIMIT | None | None | False |
| 59 | NOT_RUN_TIME_LIMIT | None | None | False |
| 60 | NOT_RUN_TIME_LIMIT | None | None | False |
| 61 | NOT_RUN_TIME_LIMIT | None | None | False |
| 62 | NOT_RUN_TIME_LIMIT | None | None | False |
| 63 | NOT_RUN_TIME_LIMIT | None | None | False |
| 64 | NOT_RUN_TIME_LIMIT | None | None | False |
| 65 | NOT_RUN_TIME_LIMIT | None | None | False |
| 66 | NOT_RUN_TIME_LIMIT | None | None | False |
| 67 | NOT_RUN_TIME_LIMIT | None | None | False |
| 68 | NOT_RUN_TIME_LIMIT | None | None | False |
| 69 | NOT_RUN_TIME_LIMIT | None | None | False |
| 70 | NOT_RUN_TIME_LIMIT | None | None | False |
| 71 | NOT_RUN_TIME_LIMIT | None | None | False |
| 72 | NOT_RUN_TIME_LIMIT | None | None | False |
| 73 | NOT_RUN_TIME_LIMIT | None | None | False |
| 74 | NOT_RUN_TIME_LIMIT | None | None | False |
| 75 | NOT_RUN_TIME_LIMIT | None | None | False |
| 76 | NOT_RUN_TIME_LIMIT | None | None | False |
| 77 | NOT_RUN_TIME_LIMIT | None | None | False |
| 78 | NOT_RUN_TIME_LIMIT | None | None | False |
| 79 | NOT_RUN_TIME_LIMIT | None | None | False |
| 80 | NOT_RUN_TIME_LIMIT | None | None | False |
| 81 | NOT_RUN_TIME_LIMIT | None | None | False |
| 82 | NOT_RUN_TIME_LIMIT | None | None | False |
| 83 | NOT_RUN_TIME_LIMIT | None | None | False |
| 84 | NOT_RUN_TIME_LIMIT | None | None | False |
| 85 | NOT_RUN_TIME_LIMIT | None | None | False |
| 86 | NOT_RUN_TIME_LIMIT | None | None | False |
| 87 | NOT_RUN_TIME_LIMIT | None | None | False |
| 88 | NOT_RUN_TIME_LIMIT | None | None | False |
| 89 | NOT_RUN_TIME_LIMIT | None | None | False |
| 90 | NOT_RUN_TIME_LIMIT | None | None | False |
| 91 | NOT_RUN_TIME_LIMIT | None | None | False |
| 92 | NOT_RUN_TIME_LIMIT | None | None | False |
| 93 | NOT_RUN_TIME_LIMIT | None | None | False |
| 94 | NOT_RUN_TIME_LIMIT | None | None | False |
| 95 | NOT_RUN_TIME_LIMIT | None | None | False |
| 96 | NOT_RUN_TIME_LIMIT | None | None | False |
| 97 | NOT_RUN_TIME_LIMIT | None | None | False |
| 98 | NOT_RUN_TIME_LIMIT | None | None | False |
| 99 | NOT_RUN_TIME_LIMIT | None | None | False |
| 100 | NOT_RUN_TIME_LIMIT | None | None | False |
