M6 联合分批成本运行结果

包装：循环；批次数：2。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：1366.682960983666；循环箱池：30。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 30 | 30 | 0 |
| b2 | 22 | 1 | 21 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：2；总距离：847.1012943169992。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 150
- e1_distance: 482.25668038833913
- e1_work: 124.03
- e1_vehicles: 20
- e1_trips: 4
- e2_distance: 364.84461392866007
- e2_work: 164.05166666666668
- e2_vehicles: 30
- e2_trips: 12
- cleaning: 6.2
- tracking: 3.1
- container_wear: 6.2
