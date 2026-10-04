M6 联合分批成本运行结果

包装：循环；批次数：2。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：4870.892269964275；循环箱池：101。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 101 | 101 | 0 |
| b2 | 93 | 53 | 40 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：2；总距离：3179.372269964275。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 505
- e1_distance: 1166.775171915279
- e1_work: 315.1366666666667
- e1_vehicles: 50
- e1_trips: 9
- e2_distance: 2012.597098048996
- e2_work: 590.3833333333333
- e2_vehicles: 95
- e2_trips: 50
- cleaning: 30.8
- tracking: 15.4
- container_wear: 30.8
