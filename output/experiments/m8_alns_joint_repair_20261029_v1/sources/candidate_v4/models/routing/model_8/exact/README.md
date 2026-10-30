# M8 直接 Gurobi 求解

`solver.py` 只调用直接 Gurobi；`strategy="milp"` 为默认，`auto` 为兼容别名。

| 文件 | 职责 |
| --- | --- |
| `model.py` | 完整数学模型入口 |
| `formulation.py` | 业务约束组装 |
| `parameters.py`、`variables.py` | 输入、变量界与决策变量 |
| `constraints.py`、`objective.py` | 业务约束与目标 |
| `milp_solver.py` | 直接求解、预算和求解器证据 |
| `solution.py` | 路线和业务决策提取 |
| `solver.py`、`__init__.py` | 公开求解入口 |

可变批次数输入默认构建完整 K 域的统一 MILP；`model.py` 的 `batch_count=k` 与 `milp_solver.py` 用于固定 K 诊断。

默认 `strengthening="physical_time"` 显式加入必要物理条件：每批领箱总量不超过箱池，
整数箱池至少为 `ceil(总箱量/K)` 且不少于最大单笔订单箱量；每个实际批次的一级配送和
回收访问各至少一次（跨全部前置仓和车辆求和）。这些条件不固定订单分批、日历或路线，
也不删减 K 域。另由最短路旅行下界计算每单最晚领箱和最早清洗时刻，
重叠的必然占用区间给出箱池下界；若某单放到后续批次，会因第一批必须先回收其他订单而
错过最晚领箱时刻，则模型显式要求该单属于第一批。没有人工指定订单排序或分组。
`strengthening="physical"` 仅启用前述批量/访问条件，不加入时间推导。
统一分块装配时，所有常数和变量界仍按对应 K 的选择变量缩放。
`strengthening="none"` 保留增强前的表达，可用于同输入、同预算的消融对照。
结果的 `info.solver_config.formulation_options.strengthening` 记录实际配置。

可选的 `preprocessing=True` 根据必要时间条件收紧订单可分配批次：若订单在第b批领箱，
此前b−1个生产性批次必须已经回库至少b−1个整数箱，且来自更早批次所服务的其他订单。
使用乐观回库时间筛出可能的前序订单，按箱量求和，迭代收紧域，并加入对应的前序回库覆盖不等式。
批次—订单匹配检查生成可独立核对的Hall亏缺证书；累计前缀需求还提供必要箱池下界。
构模前有证书的K用选择变量等于0替代，其余K仍装入同一个统一MILP。
`batch_count_candidates` 保留完整输入域，`screened_batch_counts` 保存不可行证书；
通过检查不代表该K可行，不使用ALNS方案或外部目标下界筛选。

可选的 `vehicle_symmetry=True` 在同一卫星内按每辆同型二级车首趟首任务编号排序。
它对完整车辆日程重新编号，在已有车辆启用顺序上减少等价解，不同时强制另一种可能冲突的排序。
两个新选项默认均为False，可分别开启以进行消融验证；结果配置记录实际开关。

```python
from models.routing.model_8.exact import solve_exact

result = solve_exact(problem, time_limit=300, strengthening="physical_time",
                     preprocessing=True, vehicle_symmetry=True,
                     milp_options={"formulation": "compact", "threads": 1})
```

`experiments/runs/probe_model8_physical_bounds.py` 对原输入完整允许域执行冷启动对照，
记录建模时间、优化时间、下界和求解器首次整数解事件；只对最终解进行独立业务和两周期审计。
驱动的回调只记录事件，不生成解、割或提示；正式求解入口仍直接调用无回调的 `optimize()`。

不包含构造初解、回调生成割、列生成、Benders、域树或精确 K 调度。
已退役算法及其专用测试/驱动见[研究归档](../../../../archive/exact_research_20261019/README.md)。
配置见[直接 Gurobi](../../../../docs/exact_milp_comparison.md)，数学表达见
[统一批次数 MILP](../../../../docs/unified_batch_count_milp.md)。
