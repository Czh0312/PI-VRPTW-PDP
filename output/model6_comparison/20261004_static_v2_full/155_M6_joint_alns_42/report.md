M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：1580.2539333333334；循环箱池：109。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 109 | 109 | 0 |
| b2 | 109 | 63 | 46 |
| b3 | 109 | 64 | 45 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：3；总距离：618.3456000000001。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 545
- e1_distance: 489.15280000000007
- e1_work: 145.43833333333336
- e1_vehicles: 30
- e1_trips: 9
- e2_distance: 129.19280000000003
- e2_work: 74.47000000000001
- e2_vehicles: 30
- e2_trips: 10
- cleaning: 47.2
- tracking: 23.6
- container_wear: 47.2
