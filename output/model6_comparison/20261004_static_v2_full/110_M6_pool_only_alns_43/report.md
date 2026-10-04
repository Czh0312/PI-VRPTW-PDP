M6 联合分批成本运行结果

包装：循环；批次数：2。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：1446.1156045005；循环箱池：24。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 24 | 24 | 0 |
| b2 | 24 | 8 | 16 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：2；总距离：946.1439378338332。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 120
- e1_distance: 478.03334470884465
- e1_work: 122.87
- e1_vehicles: 20
- e1_trips: 4
- e2_distance: 468.11059312498855
- e2_work: 180.1016666666667
- e2_vehicles: 25
- e2_trips: 12
- cleaning: 6.4
- tracking: 3.2
- container_wear: 6.4
