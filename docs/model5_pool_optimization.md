# M5 箱池与调度联合优化

2026-10-20 修正：M5 的实体箱数 N 是决策变量。M6、M7、M8 均在可优化 N 的基础上进一步优化分批和日历。订单的需求箱次 Q 是输入，同一实体箱可循环满足多个箱次。

| 模型 | 箱池 N、两级路线、车辆和作业时间 | 订单配送批次 | 批次日历 |
| --- | --- | --- | --- |
| M5 | 联合优化 | 给定 | 批次数、起止和分界给定 |
| M6 | 联合优化 | 优化 | 优化 K，等长批次、共同相对分界 |
| M7 | 联合优化 | 优化 | 优化 K，等长批次、逐批独立分界 |
| M8 | 联合优化 | 优化 | 优化 K，批次长度和分界均可不同 |

四个模型共用 `JointCostProfile`，当前基础费用为
`0.1*D1 + 0.05*D2 + 5*N + 0.02*Q`，来自 `config/experiments/benchmark27_v1/economic.json`。
这组系数使用合成单位。其他经济配置也支持工时、物理车辆和趟次费用；箱体投入单列，不重复加入目标。
目标最小化总成本，并非先求全局最少箱数再考虑运输。

M5 的每批供箱约束为 `N + 累计已清洗释放箱次 >= 累计领箱箱次`。
清洗恰在本批领箱时完成可用；保持截止全取、车辆复用、末态闭合和连续两周期审计。
精确模型直接建立整数 N，与路线和作业时间一起求解。ALNS 对每个候选路线和时间表计算
`N = max(pool_min, max_b(累计领箱量 - 此前已清洗释放量))`，按含 N 的总成本比较候选；
超过 `pool_max` 的候选不可行。该消元在箱池费用非负时成立，不是对最终距离解事后重新计价。
ALNS 仍是启发式，不声明箱数或总成本的全局最优。

```sh
.venv/bin/python main.py route --case config/experiments/examples/multibatch_demo.json
.venv/bin/python main.py route --case data/formal_benchmark27_one_box_v1/model_5/small/S01/case.json --solver exact --exact-time 30
```

M5 配置需要 `economic_config`；`pool_options` 可设 `pool_min`、`pool_max`，默认 `[1,Q]`。
命令行用 `--pool-min`、`--pool-max` 改允许域。`--container-pool-size N` 显式执行固定 N 诊断，
相当于同时令上下界等于 N。旧数据中的顶层 `container_pool_size` 和 policy 中的池量是输入参考值，
不再自动把当前成本模型的 N 固定；若要固定，使用上述明确的决策边界。
需求比例配置不控制当前 M5 的箱池决策。

结果保存 `objective_mode=cost`、`container_pool_size`、`pool_bounds`、`total_distance`、
`cost_components` 和经济参数哈希。独立审计使用选定 N 重放实体库存，并重算成本。
`minimum_pool_for_schedule` 仍只表示该时间表的需箱量；不能将成本最优声明解释为全局最少箱数。

历史数据及实验结果保持原字节。旧的 `M5_distance` 对照和池量扫描入口显式设置
`objective_mode=distance`；缺少费用配置的旧案例由加载器标为该历史诊断模式。
底层 `MultiBatchInstance` 继续用于固定池物理投影和历史诊断，当前成本模型使用
`FixedBatchPoolInstance`。新正式实验冻结当前 M5 参数说明，记录箱数允许域与成本目标。
旧结果不能替代修正后的 M5–M8 对比，需在新输出目录重新运行。

M5 的给定日历不必满足 M6 的等长共同分界规则；因此不自动宣称 M5 的每个可行解都属于 M6 的允许域。
