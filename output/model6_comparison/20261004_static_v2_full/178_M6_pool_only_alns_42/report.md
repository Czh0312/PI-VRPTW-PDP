M6 联合分批成本运行结果

包装：循环；批次数：2。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：1615.8048999999999；循环箱池：161。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 161 | 161 | 0 |
| b2 | 161 | 77 | 84 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：2；总距离：400.45490000000007。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 805
- e1_distance: 277.1906
- e1_work: 126.07833333333333
- e1_vehicles: 30
- e1_trips: 5
- e2_distance: 123.26430000000002
- e2_work: 100.27166666666668
- e2_vehicles: 20
- e2_trips: 10
- cleaning: 47.6
- tracking: 23.8
- container_wear: 47.6
