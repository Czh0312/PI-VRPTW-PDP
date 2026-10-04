M6 联合分批成本运行结果

包装：循环；批次数：2。
状态：OPTIMAL。
独立审计：True；已证明最优：True。
总成本：671.215738926157；循环箱池：12。
已证明不可行：False；证明范围：full_joint_batch_pool_cost_integer_time_MILP。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 12 | 11 | 1 |
| b2 | 1 | 1 | 0 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：2；总距离：445.0890722594902。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 60
- e1_distance: 229.49693827233665
- e1_work: 32.71333333333333
- e1_vehicles: 10
- e1_trips: 2
- e2_distance: 215.59213398715355
- e2_work: 102.41333333333336
- e2_vehicles: 10
- e2_trips: 3
- cleaning: 2.4000000000000004
- tracking: 1.2000000000000002
- container_wear: 2.4
