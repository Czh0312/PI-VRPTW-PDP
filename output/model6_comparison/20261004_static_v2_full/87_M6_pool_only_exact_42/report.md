M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：TIME_LIMIT。
独立审计：True；已证明最优：False。
总成本：924.8628514261452；循环箱池：12。
已证明不可行：False；证明范围：full_joint_batch_pool_cost_integer_time_MILP。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 12 | 7 | 5 |
| b2 | 5 | 4 | 1 |
| b3 | 5 | 5 | 0 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：3；总距离：655.3278514261451。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 60
- e1_distance: 344.1956456929592
- e1_work: 40.50666666666667
- e1_vehicles: 20
- e1_trips: 4
- e2_distance: 311.13220573318586
- e2_work: 116.02833333333334
- e2_vehicles: 15
- e2_trips: 6
- cleaning: 3.2
- tracking: 1.6
- container_wear: 3.2
