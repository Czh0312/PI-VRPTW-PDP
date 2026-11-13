# M1–M4 热启动独立试验

2026-10-23：针对10顾客小规模算例的300秒求解限制，增加独立的初解试验入口。
默认 `solve_exact`、公共路由服务和冻结实验配置保持原样；试验不加入遗传算法。

## 目的与入口

先区分首个可行解难找、最终方案质量不足和最优性证明缓慢，再判断是否值得开发新的初解算法。
入口是 `experiments/runs/run_single_batch_warm_start_trial.py`，通过当前项目运行环境执行模块
`experiments.runs.run_single_batch_warm_start_trial`。常用参数：

| 参数 | 含义 |
| --- | --- |
| `--output NEW_DIRECTORY` | 新的结果目录，不覆盖已有结果 |
| `--cases S02 S04 S06` | 本轮选择的三类诊断算例 |
| `--models 4` | 支持 M1–M4，默认重点检查 M4 |
| `--modes cold alns` | 冷启动与短时ALNS初解的成对对照 |
| `--total-seconds 300 --alns-seconds 30` | 每组300秒；ALNS组初解生成占其中30秒 |
| `--seed 42` | 求解及ALNS种子 |

独立试验使用 `formal_benchmark27_spacetime_v3` 的原数据、距离目标、compact表达和单线程。
`cold` 不提供初解；`alns` 在同一总预算内调用已有ALNS，然后将通过独立审计的方案导入MILP。
若ALNS没有返回有效方案，则记录这一失败，使用剩余时间冷启动MILP。

`saved` 另用于映射诊断：读取此前同模型方案，或将 M1→M2、M3→M4 的方案提升到自由选仓模型。
已有方案的历史搜索时间未计入该组预算，因此不能将它当作完整算法速度对照。

## 初解映射和证明边界

`experiments/analysis/single_batch_mip_start.py` 先重放初解，再按同质车辆的趟次数排序重编号。
它将客户分仓、路线访问、弧、作业整数时刻及选定一级箱量写入 Gurobi `Start`；辅助载重和因果序变量
由求解器补全。共享的二级内部弧按全体路线统一映射，不能被未使用的路线槽位覆盖为零。

初解只提供起点，不固定分仓、路径或时间变量的上下界，不添加约束。初解原有的最优证明和下界被清除；
最终下界及证明来自本次完整MILP，最终路径须通过独立双周期审计。观察回调只记录首解与上下界轨迹，
不增加割、惰性约束或外部下界。日志单独确认 `Loaded user MIP start` 或 `User MIP start produced solution`。

## 结果与复现材料

- [9个真实算例的已有解映射检查](../output/inspections/small10_mip_start_acceptance_20261023_v1/report.md)：每例8秒，仅验证映射接受和业务可行性。
- [S02/S04/S06的300秒成对试验](../output/experiments/small10_m4_warm_start_300s_20261023_v1/report.md)：初解准备、建模和MILP共享每组预算。

本轮三例的ALNS初解均达到冷启动最终方案的距离。S06总时间从108.37秒降到89.64秒并证明最优；
S02/S04虽更早获得可行解，但最终GAP没有改善。结论与验证范围见[试验分析](../output/experiments/small10_m4_warm_start_300s_20261023_v1/analysis.md)。

每次保存初解及其审计、最终解及其审计、Gurobi日志、观察轨迹和汇总表。
协议保存源文件哈希，结束时复核源码未变。提取和最终审计会使返回时间略超300秒。
旧300秒冷启动结果仅作历史参考，主比较使用本轮重新执行的冷启动结果；机器负载和单种子样本仍限制外推。

定向回归为 `tests/routing/test_single_batch_mip_start_trial.py`，覆盖两种路线表达的M1–M4映射、
自由选仓仍可改善初解、模型允许域不变、无效初解拒绝及原证明不继承。
