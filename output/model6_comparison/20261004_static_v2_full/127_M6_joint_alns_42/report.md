M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：3295.7241782410056；循环箱池：28。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 28 | 28 | 0 |
| b2 | 28 | 27 | 1 |
| b3 | 24 | 17 | 7 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：3；总距离：2432.820844907672。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 140
- e1_distance: 1522.1947082781123
- e1_work: 276.865
- e1_vehicles: 40
- e1_trips: 12
- e2_distance: 910.6261366295597
- e2_work: 286.03833333333336
- e2_vehicles: 45
- e2_trips: 27
- cleaning: 14.4
- tracking: 7.2
- container_wear: 14.4
