M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：988.2586333333334；循环箱池：75。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 75 | 75 | 0 |
| b2 | 75 | 69 | 6 |
| b3 | 75 | 57 | 18 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：3；总距离：320.5353。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 375
- e1_distance: 237.51440000000002
- e1_work: 73.54833333333333
- e1_vehicles: 20
- e1_trips: 4
- e2_distance: 83.0209
- e2_work: 68.675
- e2_vehicles: 15
- e2_trips: 11
- cleaning: 40.2
- tracking: 20.1
- container_wear: 40.2
