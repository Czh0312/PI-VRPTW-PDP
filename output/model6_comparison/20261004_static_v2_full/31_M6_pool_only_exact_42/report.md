M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：TIME_LIMIT。
独立审计：True；已证明最优：False。
总成本：1147.7466490694367；循环箱池：13。
已证明不可行：False；证明范围：full_joint_batch_pool_cost_integer_time_MILP。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 13 | 6 | 7 |
| b2 | 7 | 7 | 0 |
| b3 | 10 | 7 | 3 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：3；总距离：897.8366490694367。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 65
- e1_distance: 344.19564569295915
- e1_work: 55.23
- e1_vehicles: 20
- e1_trips: 4
- e2_distance: 553.6410033764776
- e2_work: 74.67999999999999
- e2_vehicles: 15
- e2_trips: 6
- cleaning: 4.0
- tracking: 2.0
- container_wear: 4.0
