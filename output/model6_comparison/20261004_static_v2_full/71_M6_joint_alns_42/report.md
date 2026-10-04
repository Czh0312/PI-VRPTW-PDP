M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：5337.127731251108；循环箱池：55。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 55 | 55 | 0 |
| b2 | 55 | 54 | 1 |
| b3 | 55 | 52 | 3 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：3；总距离：3960.786064584442。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 275
- e1_distance: 1784.4427647690193
- e1_work: 343.125
- e1_vehicles: 50
- e1_trips: 15
- e2_distance: 2176.343299815423
- e2_work: 468.7166666666667
- e2_vehicles: 90
- e2_trips: 54
- cleaning: 32.2
- tracking: 16.1
- container_wear: 32.2
