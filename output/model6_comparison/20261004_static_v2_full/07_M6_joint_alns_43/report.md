M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：634.4896058462609；循环箱池：6。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 6 | 5 | 1 |
| b2 | 6 | 6 | 0 |
| b3 | 0 | 0 | 0 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：2；总距离：461.85460584626077。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 30
- e1_distance: 180.7183030778188
- e1_work: 27.781666666666666
- e1_vehicles: 10
- e1_trips: 3
- e2_distance: 281.13630276844196
- e2_work: 82.35333333333334
- e2_vehicles: 10
- e2_trips: 4
- cleaning: 2.2
- tracking: 1.1
- container_wear: 2.2
