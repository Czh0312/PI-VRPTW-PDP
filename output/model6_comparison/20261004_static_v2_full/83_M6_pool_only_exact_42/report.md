M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：OPTIMAL。
独立审计：True；已证明最优：True。
总成本：725.1180363604788；循环箱池：6。
已证明不可行：False；证明范围：full_joint_batch_pool_cost_integer_time_MILP。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 6 | 5 | 1 |
| b2 | 6 | 6 | 0 |
| b3 | 2 | 2 | 0 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：3；总距离：542.7947030271455。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 30
- e1_distance: 313.1199039920044
- e1_work: 67.83
- e1_vehicles: 10
- e1_trips: 3
- e2_distance: 229.67479903514106
- e2_work: 49.99333333333334
- e2_vehicles: 10
- e2_trips: 5
- cleaning: 2.6
- tracking: 1.3
- container_wear: 2.6
