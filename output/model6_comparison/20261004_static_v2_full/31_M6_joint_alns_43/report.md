M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：1116.6515198002537；循环箱池：20。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 20 | 14 | 6 |
| b2 | 6 | 6 | 0 |
| b3 | 12 | 0 | 12 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：2；总距离：779.2548531335871。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 100
- e1_distance: 385.80301354330055
- e1_work: 37.910000000000004
- e1_vehicles: 20
- e1_trips: 6
- e2_distance: 393.4518395902865
- e2_work: 136.48666666666665
- e2_vehicles: 20
- e2_trips: 7
- cleaning: 4.0
- tracking: 2.0
- container_wear: 4.0
