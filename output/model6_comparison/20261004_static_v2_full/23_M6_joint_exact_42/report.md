M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：TIME_LIMIT。
独立审计：True；已证明最优：False。
总成本：828.1010766075834；循环箱池：12。
已证明不可行：False；证明范围：full_joint_batch_pool_cost_integer_time_MILP。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 12 | 12 | 0 |
| b2 | 3 | 0 | 3 |
| b3 | 3 | 2 | 1 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：2；总距离：579.6044099409168。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 60
- e1_distance: 151.4593221782554
- e1_work: 46.06
- e1_vehicles: 10
- e1_trips: 2
- e2_distance: 428.1450877626614
- e2_work: 97.43666666666667
- e2_vehicles: 20
- e2_trips: 6
- cleaning: 2.8000000000000003
- tracking: 1.4000000000000001
- container_wear: 2.8
