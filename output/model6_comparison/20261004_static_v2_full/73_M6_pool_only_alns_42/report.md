M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：4950.264172296316；循环箱池：51。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 51 | 50 | 1 |
| b2 | 51 | 51 | 0 |
| b3 | 51 | 48 | 3 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：3；总距离：3645.134172296316。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 255
- e1_distance: 1512.8423912439648
- e1_work: 336.33500000000004
- e1_vehicles: 50
- e1_trips: 15
- e2_distance: 2132.2917810523513
- e2_work: 440.295
- e2_vehicles: 85
- e2_trips: 49
- cleaning: 29.8
- tracking: 14.9
- container_wear: 29.8
