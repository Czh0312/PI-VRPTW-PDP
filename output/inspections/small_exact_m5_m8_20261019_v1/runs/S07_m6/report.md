M6 联合分批成本运行结果

包装：循环；批次数：None。
状态：TIME_LIMIT。
独立审计：False；已证明最优：False。
总成本：None；循环箱池：18。
已证明不可行：False；证明范围：full_model_6_all_K_joint_cost_integer_time_MILP。
必要容器池下界：未计算；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |

连续两个大周期重放：False。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / (60 × time_tick_subdivision)，细分倍率缺省为1。

启用配送批次数：None；总距离：None。
成本来源类型：synthetic_mechanism；单位：synthetic_units。
每个计入批次数的批次都必须有配送和回运；纯回收趟次显式计费。初始箱体投资单列。
截点仅决定本轮回运资格；二级车辆在第二阶段仍可配送和回收。


批次数允许域：[1, 2, 3, 4]；选定周期批次数：None。
实际空箱回运轮数：None。
统一分界比例：None。
批次数、路线、箱池和日历在一个 MILP 中联合优化；一次 Gurobi 求解。
完整候选域已建模：True；全局最优已证明：False；全局下界：64.91039816734657。
