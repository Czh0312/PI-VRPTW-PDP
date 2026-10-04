M6 联合分批成本运行结果

包装：循环；批次数：2。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：698.4755020029309；循环箱池：15。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 15 | 14 | 1 |
| b2 | 1 | 1 | 0 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：2；总距离：390.2755020029309。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 75
- e1_distance: 185.97588084733323
- e1_work: 31.078333333333337
- e1_vehicles: 10
- e1_trips: 2
- e2_distance: 204.2996211555977
- e2_work: 163.62166666666667
- e2_vehicles: 15
- e2_trips: 4
- cleaning: 3.0
- tracking: 1.5
- container_wear: 3.0
