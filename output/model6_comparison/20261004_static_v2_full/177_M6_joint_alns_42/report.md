M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：1322.9048000000003；循环箱池：77。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 77 | 77 | 0 |
| b2 | 77 | 71 | 6 |
| b3 | 69 | 39 | 30 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：3；总距离：537.1498。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 385
- e1_distance: 434.18330000000003
- e1_work: 158.91
- e1_vehicles: 30
- e1_trips: 8
- e2_distance: 102.96650000000001
- e2_work: 73.34500000000001
- e2_vehicles: 25
- e2_trips: 12
- cleaning: 37.4
- tracking: 18.7
- container_wear: 37.4
