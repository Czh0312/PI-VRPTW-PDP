M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：747.2007666666666；循环箱池：60。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 60 | 60 | 0 |
| b2 | 60 | 57 | 3 |
| b3 | 23 | 0 | 23 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：2；总距离：234.11409999999998。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 300
- e1_distance: 182.7995
- e1_work: 47.64
- e1_vehicles: 20
- e1_trips: 3
- e2_distance: 51.31459999999998
- e2_work: 61.94666666666667
- e2_vehicles: 15
- e2_trips: 7
- cleaning: 23.400000000000002
- tracking: 11.700000000000001
- container_wear: 23.4
