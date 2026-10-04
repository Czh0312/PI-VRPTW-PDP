M6 联合分批成本运行结果

包装：循环；批次数：2。
状态：TIME_LIMIT。
独立审计：True；已证明最优：False。
总成本：861.9596666666666；循环箱池：95。
已证明不可行：False；证明范围：full_joint_batch_pool_cost_integer_time_MILP。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 95 | 95 | 0 |
| b2 | 95 | 23 | 72 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：2；总距离：182.26799999999997。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 475
- e1_distance: 114.1513
- e1_work: 70.505
- e1_vehicles: 10
- e1_trips: 2
- e2_distance: 68.11669999999998
- e2_work: 38.18666666666667
- e2_vehicles: 15
- e2_trips: 10
- cleaning: 23.6
- tracking: 11.8
- container_wear: 23.6
