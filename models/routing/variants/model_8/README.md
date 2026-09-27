# Model 8 v3：两级路径与空箱容量协作联合优化

当前入口不先求 M3，不固定 E1/E2 路线、配送归属/顺序或已投入车辆集合。
在配置车队上限内联合决定路径、配送回收任务和异步空箱传递。

- `model.py`：完整联合紧凑模型；当前规则下精确可行域与 M6 相同。
- `exact_solver.py`：直接求解一个完整 MILP；全模型闭合才报告最优。
- `state.py`：完整 E1/E2 载荷、时间、任务覆盖及空箱库存审计。
- `initial_solution.py`：普通可行初始构造，不调用 M3 求解器。
- `neighborhoods.py`、`e1_moves.py`：容量协作与 E1 路线邻域。
- `../../alns/variants/model_8.py`：一个循环联合优化全部路线与交接。
- `two_stage_v2/`：已归档的固定骨架及放开 E2 配送实验版本。
- `legacy/`：更早的冻结全部原任务版本。

允许同点多车、不等量、异步分取；每个箱子最多一次换车。
`allow_transfers=False` 是全路径自由优化的无传递对照。
可选热启动仅提供起点，不形成任何路线固定条件。

详见 [当前设计](../../../../docs/model8_capacity_cooperation.md)。
