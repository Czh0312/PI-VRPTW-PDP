M6 联合分批成本运行结果

包装：循环；批次数：3。
状态：FEASIBLE。
独立审计：True；已证明最优：False。
总成本：7007.772933830096；循环箱池：60。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |
| b1 | 60 | 50 | 10 |
| b2 | 54 | 54 | 0 |
| b3 | 53 | 51 | 2 |

连续两个大周期重放：True。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：3；总距离：5502.387933830096。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留固定截止回收义务；纯回收趟次显式计费。初始箱体投资单列。

- container_pool: 300
- e1_distance: 2247.7967322276104
- e1_work: 318.9916666666667
- e1_vehicles: 50
- e1_trips: 14
- e2_distance: 3254.5912016024854
- e2_work: 601.8933333333333
- e2_vehicles: 90
- e2_trips: 53
- cleaning: 31.0
- tracking: 15.5
- container_wear: 31.0
