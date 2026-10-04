M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：TIME_LIMIT。
独立审计：True；已证明最优：False。
总成本：954.5124319555243；循环箱池：8。
已证明不可行：False；证明范围：full_joint_batch_pool_cost_integer_time_MILP。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 8 | 8 | 0 |
| b2 | 4 | 4 | 0 |
| b3 | 7 | 5 | 2 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：3；总距离：730.6074319555244。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 40
- e1_distance: 303.2950505180096
- e1_work: 67.83666666666667
- e1_vehicles: 10
- e1_trips: 3
- e2_distance: 427.3123814375148
- e2_work: 78.56833333333333
- e2_vehicles: 10
- e2_trips: 6
- cleaning: 3.4000000000000004
- tracking: 1.7000000000000002
- container_wear: 3.4
