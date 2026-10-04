# 模型6与模型5：静态联合分批对比

本次运行全部采用 synthetic_units 合成成本单位，用于检查算法与机制；不代表真实人民币成本或企业节约。

精确求解每次 20 秒；ALNS 每次 1 秒、最多 1000 次迭代，种子 [42, 43, 44]。所有结果均独立审计，包括连续两周期的容器与车辆可复用性。

模型5固定原分批和全需求容器池，以距离为目标。其成本是对距离目标所得调度的重算，可能受同距离解的等待和路径选择影响。M6固定方案用同一成本目标优化原分批与原容器池，作为判断联合决策收益的更严格基准。

| 算例 | M5精确解重算成本 | M6固定方案成本 | M6联合精确成本 | M6 ALNS 最好/均值/最差 | 联合解容器/配送批次/一级趟/二级趟 |
|---|---:|---:|---:|---:|---:|
| consolidation_low_pool | 67.1000 | 62.3000 | 47.1000 | 47.1000/47.1000/47.1000 | 2/1/1/1 |
| reuse_high_pool | 117.1000 | 112.3000 | 86.0000 | 86.0000/86.0000/86.0000 | 1/2/2/2 |
| late_return_inactive_delivery | 58.2000 | 50.9000 | 50.9000 | 50.9000/50.9000/50.9000 | 1/1/2/2 |
| two_satellites | 123.6132 | 109.9132 | 74.2000 | 74.2000/74.2000/74.2000 | 2/2/2/2 |

## 分解对比与证明范围

| 算例 | 处理 | 方法/种子 | 成本 | 距离 | 容器池 | 配送批次 | 一级/二级趟数 | 状态 | 最优证明 | 间隙 |
|---|---|---|---:|---:|---:|---:|---|---|---|---:|
| consolidation_low_pool | M5_distance | exact/42 | 67.1000 | 26.0000 | 2 | 2 | 2/2 | OPTIMAL | True | — |
| consolidation_low_pool | M5_distance | alns/42 | 66.0000 | 26.0000 | 2 | 2 | 2/2 | FEASIBLE | False | — |
| consolidation_low_pool | M5_distance | alns/43 | 66.0000 | 26.0000 | 2 | 2 | 2/2 | FEASIBLE | False | — |
| consolidation_low_pool | M5_distance | alns/44 | 66.0000 | 26.0000 | 2 | 2 | 2/2 | FEASIBLE | False | — |
| consolidation_low_pool | M6_fixed | exact/42 | 62.3000 | 26.0000 | 2 | 2 | 2/2 | OPTIMAL | True | — |
| consolidation_low_pool | M6_pool_only | exact/42 | 61.0000 | 26.0000 | 1 | 2 | 2/2 | OPTIMAL | True | — |
| consolidation_low_pool | M6_batch_only | exact/42 | 47.1000 | 14.0000 | 2 | 1 | 1/1 | OPTIMAL | True | — |
| consolidation_low_pool | M6_joint | exact/42 | 47.1000 | 14.0000 | 2 | 1 | 1/1 | OPTIMAL | True | — |
| consolidation_low_pool | M6_joint | alns/42 | 47.1000 | 14.0000 | 2 | 1 | 1/1 | FEASIBLE | False | 0.0000 |
| consolidation_low_pool | M6_joint | alns/43 | 47.1000 | 14.0000 | 2 | 1 | 1/1 | FEASIBLE | False | 0.0000 |
| consolidation_low_pool | M6_joint | alns/44 | 47.1000 | 14.0000 | 2 | 1 | 1/1 | FEASIBLE | False | 0.0000 |
| reuse_high_pool | M5_distance | exact/42 | 117.1000 | 26.0000 | 2 | 2 | 2/2 | OPTIMAL | True | — |
| reuse_high_pool | M5_distance | alns/42 | 116.0000 | 26.0000 | 2 | 2 | 2/2 | FEASIBLE | False | — |
| reuse_high_pool | M5_distance | alns/43 | 116.0000 | 26.0000 | 2 | 2 | 2/2 | FEASIBLE | False | — |
| reuse_high_pool | M5_distance | alns/44 | 116.0000 | 26.0000 | 2 | 2 | 2/2 | FEASIBLE | False | — |
| reuse_high_pool | M6_fixed | exact/42 | 112.3000 | 26.0000 | 2 | 2 | 2/2 | OPTIMAL | True | — |
| reuse_high_pool | M6_pool_only | exact/42 | 86.0000 | 26.0000 | 1 | 2 | 2/2 | OPTIMAL | True | — |
| reuse_high_pool | M6_batch_only | exact/42 | 97.1000 | 14.0000 | 2 | 1 | 1/1 | OPTIMAL | True | — |
| reuse_high_pool | M6_joint | exact/42 | 86.0000 | 26.0000 | 1 | 2 | 2/2 | OPTIMAL | True | — |
| reuse_high_pool | M6_joint | alns/42 | 86.0000 | 26.0000 | 1 | 2 | 2/2 | FEASIBLE | False | 0.0000 |
| reuse_high_pool | M6_joint | alns/43 | 86.0000 | 26.0000 | 1 | 2 | 2/2 | FEASIBLE | False | 0.0000 |
| reuse_high_pool | M6_joint | alns/44 | 86.0000 | 26.0000 | 1 | 2 | 2/2 | FEASIBLE | False | 0.0000 |
| late_return_inactive_delivery | M5_distance | exact/42 | 58.2000 | 22.0000 | 1 | 1 | 2/1 | OPTIMAL | True | — |
| late_return_inactive_delivery | M5_distance | alns/42 | 53.6000 | 22.0000 | 1 | 1 | 2/1 | FEASIBLE | False | — |
| late_return_inactive_delivery | M5_distance | alns/43 | 53.6000 | 22.0000 | 1 | 1 | 2/1 | FEASIBLE | False | — |
| late_return_inactive_delivery | M5_distance | alns/44 | 53.6000 | 22.0000 | 1 | 1 | 2/1 | FEASIBLE | False | — |
| late_return_inactive_delivery | M6_fixed | exact/42 | 50.9000 | 24.0000 | 1 | 1 | 2/2 | OPTIMAL | True | — |
| late_return_inactive_delivery | M6_pool_only | exact/42 | 50.9000 | 24.0000 | 1 | 1 | 2/2 | OPTIMAL | True | — |
| late_return_inactive_delivery | M6_batch_only | exact/42 | 50.9000 | 24.0000 | 1 | 1 | 2/2 | OPTIMAL | True | — |
| late_return_inactive_delivery | M6_joint | exact/42 | 50.9000 | 24.0000 | 1 | 1 | 2/2 | OPTIMAL | True | — |
| late_return_inactive_delivery | M6_joint | alns/42 | 50.9000 | 24.0000 | 1 | 1 | 2/2 | FEASIBLE | False | 0.0000 |
| late_return_inactive_delivery | M6_joint | alns/43 | 50.9000 | 24.0000 | 1 | 1 | 2/2 | FEASIBLE | False | 0.0000 |
| late_return_inactive_delivery | M6_joint | alns/44 | 50.9000 | 24.0000 | 1 | 1 | 2/2 | FEASIBLE | False | 0.0000 |
| two_satellites | M5_distance | exact/42 | 123.6132 | 53.2132 | 4 | 2 | 2/4 | OPTIMAL | True | — |
| two_satellites | M5_distance | alns/42 | 110.3132 | 53.2132 | 4 | 2 | 2/4 | FEASIBLE | False | — |
| two_satellites | M5_distance | alns/43 | 110.3132 | 53.2132 | 4 | 2 | 2/4 | FEASIBLE | False | — |
| two_satellites | M5_distance | alns/44 | 110.3132 | 53.2132 | 4 | 2 | 2/4 | FEASIBLE | False | — |
| two_satellites | M6_fixed | exact/42 | 109.9132 | 53.2132 | 4 | 2 | 2/4 | OPTIMAL | True | — |
| two_satellites | M6_pool_only | exact/42 | 107.8132 | 53.2132 | 3 | 2 | 2/4 | OPTIMAL | True | — |
| two_satellites | M6_batch_only | exact/42 | 83.6421 | 32.1421 | 4 | 1 | 1/2 | OPTIMAL | True | — |
| two_satellites | M6_joint | exact/42 | 74.2000 | 28.0000 | 2 | 2 | 2/2 | OPTIMAL | True | — |
| two_satellites | M6_joint | alns/42 | 74.2000 | 28.0000 | 2 | 2 | 2/2 | FEASIBLE | False | 0.0000 |
| two_satellites | M6_joint | alns/43 | 74.2000 | 28.0000 | 2 | 2 | 2/2 | FEASIBLE | False | 0.0000 |
| two_satellites | M6_joint | alns/44 | 74.2000 | 28.0000 | 2 | 2 | 2/2 | FEASIBLE | False | 0.0000 |

M6_fixed：原分批与原容器池均固定；M6_pool_only：只放开容器池；M6_batch_only：只放开分批；M6_joint：同时放开分批和容器池。各组均使用成本目标并联合优化两级路径。不同放开顺序的收益不应相加。

表中的间隙单位为百分比，仅对 M6_joint ALNS 相对于已证明最优的 M6_joint 精确值计算；启发式结果不声称全局最优。M5 的最优证明仅针对原固定问题的距离目标。

固定候选时段与回收截止始终保留：关闭配送批次不会取消该时段的回收义务。清洗和追踪按全部订单使用量计费，本组订单不可拆分且需求固定，因此这些费用为常数。容器使用磨损与池规模占用费分开；初始投资另列且不重复计入周期成本。

合成算例的距离换算为每坐标单位1公里、时间刻度1分钟，等待计入占用工时，趟间闲置薪酬不计。车辆按唯一物理车辆计费，趟次按实际路线计费。候选时段、释放时刻、允许批次和所有价格均在 inputs 快照中。

复现命令：

    .venv/bin/python -m experiments.runs.run_model6_comparison --manifest data/model6/mechanism_v1/manifest.json --exact-time 20 --time 1 --iterations 1000 --seeds 42 43 44

逐次运行的原始路线、独立审计、成本分项在各运行目录；rows.csv 保留失败及超时；experiment.json 包含预算、完整输入和源码哈希。

最终验证：全库回归3103项通过；证据校验和快照处理补充后，模型6专项36项通过。当前代码重新审计全部44个结果，全部有效。记录见 [validation.json](validation.json)，原始测试XML和独立复核明细一并保存。
