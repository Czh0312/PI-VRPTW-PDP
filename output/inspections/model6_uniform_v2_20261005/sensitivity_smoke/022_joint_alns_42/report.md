M6 联合分批成本运行结果

包装：循环；批次数：None。
状态：HEURISTIC_NO_SOLUTION。
独立审计：False；已证明最优：False。
总成本：None；循环箱池：12。
已证明不可行：False；证明范围：无。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |

连续两个大周期重放：False。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

启用配送批次数：None；总距离：None。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
关闭配送批次仍保留回收义务；纯回收趟次显式计费。初始箱体投资单列。
截点仅决定本轮回运资格；二级车辆在第二阶段仍可配送和回收。


均匀分批搜索范围：[1, 2, 3]；选定周期批次数：None。
每批时长/分钟：None；统一分界比例：None；实际空箱回运轮数：None。
所有配置批次数均解决，才能声明整个均匀分批模型的成本最优；单个K最优不足以证明全局最优。

| K | 状态 | 有效成本 | 箱数 | 该K成本最优已证明 |
| --- | --- | ---: | ---: | --- |
| 1 | HEURISTIC_NO_SOLUTION | None | None | False |
| 2 | HEURISTIC_NO_SOLUTION | None | None | False |
| 3 | HEURISTIC_NO_SOLUTION | None | None | False |
