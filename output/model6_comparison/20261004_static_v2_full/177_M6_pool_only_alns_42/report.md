M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：1081.7541333333336；循环箱池：77。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 77 | 77 | 0 |
| b2 | 77 | 63 | 14 |
| b3 | 77 | 47 | 30 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：3；总距离：409.26079999999996。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 385
- e1_distance: 313.62669999999997
- e1_work: 75.60666666666667
- e1_vehicles: 20
- e1_trips: 5
- e2_distance: 95.6341
- e2_work: 68.38666666666667
- e2_vehicles: 15
- e2_trips: 10
- cleaning: 37.4
- tracking: 18.7
- container_wear: 37.4
