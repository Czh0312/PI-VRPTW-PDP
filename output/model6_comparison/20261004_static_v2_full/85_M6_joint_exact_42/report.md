M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：TIME_LIMIT。
独立审计：True；已证明最优：False。
总成本：676.3306917642471；循环箱池：15。
已证明不可行：False；证明范围：full_joint_batch_pool_cost_integer_time_MILP。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 15 | 15 | 0 |
| b2 | 0 | 0 | 0 |
| b3 | 0 | 0 | 0 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：1；总距离：397.1306917642472。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 75
- e1_distance: 213.81783137139053
- e1_work: 13.028333333333334
- e1_vehicles: 20
- e1_trips: 3
- e2_distance: 183.31286039285672
- e2_work: 142.67166666666668
- e2_vehicles: 15
- e2_trips: 3
- cleaning: 3.0
- tracking: 1.5
- container_wear: 3.0
