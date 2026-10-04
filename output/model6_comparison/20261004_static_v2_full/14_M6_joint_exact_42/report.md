M6 联合分批成本运行结果

包装：循环；批次数：2。
状态：TIME_LIMIT。
独立审计：True；已证明最优：False。
总成本：679.1951363135287；循环箱池：15。
已证明不可行：False；证明范围：full_joint_batch_pool_cost_integer_time_MILP。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 15 | 15 | 0 |
| b2 | 8 | 0 | 8 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：1；总距离：414.16680298019537。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 75
- e1_distance: 185.9907068776442
- e1_work: 50.37833333333334
- e1_vehicles: 20
- e1_trips: 2
- e2_distance: 228.17609610255118
- e2_work: 91.15
- e2_vehicles: 15
- e2_trips: 4
- cleaning: 3.0
- tracking: 1.5
- container_wear: 3.0
