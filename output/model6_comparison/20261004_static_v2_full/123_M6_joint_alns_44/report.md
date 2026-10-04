M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：2131.415707551691；循环箱池：20。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 20 | 20 | 0 |
| b2 | 14 | 8 | 6 |
| b3 | 19 | 15 | 4 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：3；总距离：1547.165707551691。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 100
- e1_distance: 904.4199599480484
- e1_work: 183.61666666666667
- e1_vehicles: 30
- e1_trips: 8
- e2_distance: 642.7457476036427
- e2_work: 187.13333333333333
- e2_vehicles: 35
- e2_trips: 19
- cleaning: 8.6
- tracking: 4.3
- container_wear: 8.6
