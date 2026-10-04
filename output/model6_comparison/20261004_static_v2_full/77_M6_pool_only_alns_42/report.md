M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：786.2768885794231；循环箱池：5。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 5 | 5 | 0 |
| b2 | 5 | 2 | 3 |
| b3 | 5 | 4 | 1 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：3；总距离：587.4185552460897。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 25
- e1_distance: 187.5071249536235
- e1_work: 65.295
- e1_vehicles: 20
- e1_trips: 3
- e2_distance: 399.9114302924662
- e2_work: 64.06333333333333
- e2_vehicles: 10
- e2_trips: 6
- cleaning: 2.2
- tracking: 1.1
- container_wear: 2.2
