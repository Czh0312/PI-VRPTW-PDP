M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：1246.4361666666668；循环箱池：70。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 70 | 65 | 5 |
| b2 | 70 | 70 | 0 |
| b3 | 51 | 46 | 5 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：3；总距离：568.1145000000001。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 350
- e1_distance: 488.2920000000001
- e1_work: 93.89333333333333
- e1_vehicles: 30
- e1_trips: 9
- e2_distance: 79.82249999999999
- e2_work: 70.92833333333333
- e2_vehicles: 25
- e2_trips: 9
- cleaning: 36.2
- tracking: 18.1
- container_wear: 36.2
