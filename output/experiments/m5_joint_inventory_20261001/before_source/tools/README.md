# 数据与审计工具

`data/build_static_batch_suite.py` 生成固定批次配对，`data/static_batch_dataset.py` 选择并验证输入。
`audit/audit_static_batch_suite.py` 验证构造时间表和连续周期。
`data/build_lade_real_case.py`、`data/generate_zhou_later_pickup_dataset.py` 与 `audit/audit_r3pack_dataset.py` 保留数据来源重建和参数审计能力。
旧模型专属绘图和过时场景生成工具已归档。

`audit/benchmark_multibatch_primal.py` 冻结 M5 输入和源码，顺序比较开启/关闭独立初解的
完整 MILP 求解；每次保留求解日志、方案及独立审计，使用相同总预算和随机种子。
添加 `--mode formulation` 可比较本轮 M5 强化与关闭强化后的模型，两个版本都保留初解。
添加 `--mode incumbent` 可在同一完整 MILP 下比较原初解策略与扩展插入、一层路线合并策略。
添加 `--mode root` 可比较二层线性条件关系与趟位压缩前后的完整 MILP，两组保留相同初解策略。
添加 `--mode routes` 可比较原二层内部弧副本与默认按规模共享的表达，见
[M5 内部弧压缩报告](../docs/m5_exact_routes_20261001.md)。
`--initial-results` 可为每个输入提供一个已审计方案；工具冻结并重新审计这些方案，供两组共用，
各组自身初解构造和建模仍计入总预算。该选项适用于 formulation、root、routes、loads、bounds、distance-cuts 对照。
`--mode loads` 比较出仓载荷总量的重复展开与显式变量提取，后者为实验选项，默认关闭。
`--mode bounds` 比较仅使用 Gurobi 界与合并独立 M5 距离证书，证书计算计入原总预算，
见 [M5 距离下界与根节点诊断](../docs/m5_exact_bound_certificate_20261001.md)。
`--mode distance-cuts` 比较只报告证书、总距离行、按批次/仓分项距离行；
`--variants reference components` 可只运行其中两组。距离行保持可选，见
[M5 距离证书约束](../docs/m5_exact_certificate_cuts_20261001.md)。

`audit/benchmark_multibatch_route_cg.py` 冻结 M5 输入、源码和通过审计的同一初解，
顺序比较现有 MILP 与二层路线覆盖列生成的有效下界。支持 `--cases`、`--initial-results`、
`--time`、`--pricing-time`、`--seeds` 及 `--variants milp cg`；固定初解 gap 用于分离下界收益，
MILP 自己改善的上界另行保存。CG 只输出松弛证书，见
[M5 列生成实现与验证](../docs/m5_route_cg_20261001.md)。

该工具另支持 `--variants cg cg-e1`，比较原二层 CG 与加入一级整路线/清洗库存松弛的证书。
`--e1-time 10` 是原总预算内的一级上限，并最多占剩余时间的三分之一；结果额外保存一级界、
完整枚举状态和独立一级 MPS。见 [一级路线下界同预算对照](../docs/m5_e1_inventory_20261001.md)。

`audit/benchmark_multibatch_e1_polish.py` 比较 `fixed`、`retimed` 的独立可行方案改进，
或 `cg`/`retimed-cg`、`milp`/`retimed-milp` 的同总预算组合。
它冻结同一已审计起始方案，记录真实可行上界及对应有效 gap，避免把受限子问题界作为全局界。
重优化包含在 `--time` 总预算内，见 [一级路线与二级时刻联合改进](../docs/m5_e1_polish_20261001.md)。

`audit/benchmark_multibatch_route_pool.py` 比较 `reorder`/`pool` 的二层路线候选搜索，
及 `cg`/`pool-cg`、`milp`/`pool-milp` 的同总预算组合。每轮保存候选列、输入输出及 MPS，
只使用当前初解生成候选，见 [二层路线池整数主问题](../docs/m5_route_pool_20261001.md)。

`audit/benchmark_multibatch_enumerated_pricing.py` 在冻结的同一已审计上界和总预算下，
比较 `cg` 原 MILP 定价、`hybrid` 完整路线枚举/回退，以及 `fallback` 强制中断枚举。
支持 `--enumeration-time`、`--enumeration-states`；完整路线域和完整覆盖 LP 压缩导出，
见 [二层完整枚举与独立复核](../docs/m5_enumerated_pricing_20261001.md)。

`audit/benchmark_multibatch_integer_cover.py` 冻结相同输入、完整可行上界与源码，
在原总预算内比较完整路线覆盖 `lp` 与 `integer`，另提供强制中断枚举的 `fallback`。
`--integer-time 15` 是每仓整数阶段上限；完整二元模型和连续模型分别压缩导出。
见 [完整整数覆盖与全矩阵复核](../docs/m5_integer_cover_20261001.md)。
