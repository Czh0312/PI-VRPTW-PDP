# 模型6与模型5：静态联合分批对比

本次运行全部采用 synthetic_units 合成成本单位，用于检查算法与机制；不代表真实人民币成本或企业节约。

精确求解每次 15 秒；ALNS 每次 8 秒、最多 30000 次迭代，种子 [42, 43, 44]。所有结果均独立审计，包括连续两周期的容器与车辆可复用性。

模型5固定原分批和预设容器池，以距离为目标。其成本是对距离目标所得调度的重算，可能受同距离解的等待和路径选择影响。M6固定/仅优化池的配置保持原分批，用同一成本目标优化，区分预设池、目标函数变化与自由分批收益。

## 容器与配送批次

| 算例 | 订单/区域数 | 当日需求箱次Q | M5预设箱池 | M5路线最小需要/配送批次 | 原分批成本优化的箱池/批次 | M6联合箱池/批次 | M5→M6一级/二级趟数 |
|---|---:|---:|---:|---:|---:|---:|---|
| lade_0823_10aoi | 10 | 131 | 131 | 68/3 | 68/3 | 68/2 | 3/8 → 3/9 |
| lade_0823_20aoi | 20 | 201 | 201 | 135/3 | 75/3 | 75/3 | 3/11 → 4/11 |
| lade_0823_30aoi | 30 | 255 | 255 | 223/3 | 109/3 | 109/3 | 3/14 → 4/11 |

Q是一个周期的累计用箱需求，不是必须同时配备的实体箱数。模型5的预设池与其所得时间表的最小可行池分开列示，不能把预设池直接称为最低需要。M5选取有效运行中的最短距离方案；M6选取有效运行中的最低成本方案。各次结果和种子全部保留，时间表所需最小池不构成联合问题最小池的证明。

## 成本与限时结果

| 算例 | M5精确解重算成本 | M6固定方案成本 | M6联合精确成本 | M6 ALNS 最好/均值/最差 |
|---|---:|---:|---:|---:|
| lade_0823_10aoi | 1183.6028 | — | 773.7892 | 801.2777/812.9465/835.5971 |
| lade_0823_20aoi | 1684.1688 | — | — | 988.2586/1045.0484/1095.2985 |
| lade_0823_30aoi | 2075.0051 | — | — | 1292.8246/1381.1170/1489.2867 |

## 分解对比与证明范围

| 算例 | 处理 | 方法/种子 | 成本 | 距离 | 容器池 | 配送批次 | 一级/二级趟数 | 状态 | 最优证明 | 间隙 |
|---|---|---|---:|---:|---:|---:|---|---|---|---:|
| lade_0823_10aoi | M5_distance | exact/42 | 1183.6028 | 230.2961 | 131 | 3 | 3/8 | OPTIMAL | True | — |
| lade_0823_10aoi | M5_distance | alns/42 | 1145.3294 | 230.2961 | 131 | 3 | 3/8 | FEASIBLE | False | — |
| lade_0823_10aoi | M5_distance | alns/43 | 1211.6944 | 276.6994 | 131 | 3 | 4/8 | FEASIBLE | False | — |
| lade_0823_10aoi | M5_distance | alns/44 | 1211.9661 | 276.6994 | 131 | 3 | 4/8 | FEASIBLE | False | — |
| lade_0823_10aoi | M6_pool_only | exact/42 | 795.1527 | 238.5927 | 68 | 3 | 3/9 | TIME_LIMIT | False | — |
| lade_0823_10aoi | M6_pool_only | alns/42 | 818.5726 | 233.8326 | 68 | 3 | 3/8 | FEASIBLE | False | — |
| lade_0823_10aoi | M6_pool_only | alns/43 | 865.1371 | 285.8288 | 68 | 3 | 4/9 | FEASIBLE | False | — |
| lade_0823_10aoi | M6_pool_only | alns/44 | 821.7444 | 238.5927 | 68 | 3 | 3/9 | FEASIBLE | False | — |
| lade_0823_10aoi | M6_joint | exact/42 | 773.7892 | 237.3975 | 68 | 2 | 3/9 | TIME_LIMIT | False | — |
| lade_0823_10aoi | M6_joint | alns/42 | 801.2777 | 233.0877 | 68 | 2 | 3/8 | FEASIBLE | False | — |
| lade_0823_10aoi | M6_joint | alns/43 | 835.5971 | 240.9121 | 73 | 2 | 3/8 | FEASIBLE | False | — |
| lade_0823_10aoi | M6_joint | alns/44 | 801.9647 | 246.7630 | 68 | 2 | 3/9 | FEASIBLE | False | — |
| lade_0823_20aoi | M5_distance | exact/42 | 1684.1688 | 260.8588 | 201 | 3 | 3/11 | TIME_LIMIT | False | — |
| lade_0823_20aoi | M5_distance | alns/42 | 1801.3256 | 352.6389 | 201 | 3 | 5/10 | FEASIBLE | False | — |
| lade_0823_20aoi | M5_distance | alns/43 | 1751.7714 | 306.6214 | 201 | 3 | 4/9 | FEASIBLE | False | — |
| lade_0823_20aoi | M5_distance | alns/44 | 1592.7904 | 263.8404 | 201 | 3 | 3/10 | FEASIBLE | False | — |
| lade_0823_20aoi | M6_pool_only | exact/42 | — | — | None | None | None/None | TIME_LIMIT | False | — |
| lade_0823_20aoi | M6_pool_only | alns/42 | 1004.1670 | 319.9970 | 75 | 3 | 4/10 | FEASIBLE | False | — |
| lade_0823_20aoi | M6_pool_only | alns/43 | 991.0794 | 330.1927 | 75 | 3 | 4/10 | FEASIBLE | False | — |
| lade_0823_20aoi | M6_pool_only | alns/44 | 934.9704 | 267.9737 | 75 | 3 | 3/9 | FEASIBLE | False | — |
| lade_0823_20aoi | M6_joint | exact/42 | — | — | None | None | None/None | TIME_LIMIT | False | — |
| lade_0823_20aoi | M6_joint | alns/42 | 1051.5880 | 362.4413 | 75 | 3 | 5/9 | FEASIBLE | False | — |
| lade_0823_20aoi | M6_joint | alns/43 | 1095.2985 | 419.6302 | 75 | 3 | 6/9 | FEASIBLE | False | — |
| lade_0823_20aoi | M6_joint | alns/44 | 988.2586 | 320.5353 | 75 | 3 | 4/11 | FEASIBLE | False | — |
| lade_0823_30aoi | M5_distance | exact/42 | 2075.0051 | 293.3768 | 255 | 3 | 3/14 | TIME_LIMIT | False | — |
| lade_0823_30aoi | M5_distance | alns/42 | 2154.7848 | 335.2331 | 255 | 3 | 4/11 | FEASIBLE | False | — |
| lade_0823_30aoi | M5_distance | alns/43 | 2227.1696 | 378.2429 | 255 | 3 | 5/11 | FEASIBLE | False | — |
| lade_0823_30aoi | M5_distance | alns/44 | 2168.3993 | 332.3826 | 255 | 3 | 4/11 | FEASIBLE | False | — |
| lade_0823_30aoi | M6_pool_only | exact/42 | — | — | None | None | None/None | TIME_LIMIT | False | — |
| lade_0823_30aoi | M6_pool_only | alns/42 | 1377.4424 | 459.4374 | 109 | 3 | 6/13 | FEASIBLE | False | — |
| lade_0823_30aoi | M6_pool_only | alns/43 | 1252.6088 | 352.5155 | 109 | 3 | 4/12 | FEASIBLE | False | — |
| lade_0823_30aoi | M6_pool_only | alns/44 | 1205.8018 | 320.7335 | 109 | 3 | 3/12 | FEASIBLE | False | — |
| lade_0823_30aoi | M6_joint | exact/42 | — | — | None | None | None/None | TIME_LIMIT | False | — |
| lade_0823_30aoi | M6_joint | alns/42 | 1361.2396 | 457.9313 | 109 | 3 | 6/12 | FEASIBLE | False | — |
| lade_0823_30aoi | M6_joint | alns/43 | 1489.2867 | 497.7100 | 114 | 3 | 7/11 | FEASIBLE | False | — |
| lade_0823_30aoi | M6_joint | alns/44 | 1292.8246 | 359.8496 | 109 | 3 | 4/11 | FEASIBLE | False | — |

M6_fixed：原分批与原容器池均固定；M6_pool_only：只放开容器池；M6_batch_only：只放开分批；M6_joint：同时放开分批和容器池。各组均使用成本目标并联合优化两级路径。不同放开顺序的收益不应相加。

表中的间隙单位为百分比，仅对 M6_joint ALNS 相对于已证明最优的 M6_joint 精确值计算；启发式结果不声称全局最优。M5 的最优证明仅针对原固定问题的距离目标。

固定候选时段与回收截止始终保留：关闭配送批次不会取消该时段的回收义务。清洗和追踪按全部订单使用量计费，本组订单不可拆分且需求固定，因此这些费用为常数。容器使用磨损与池规模占用费分开；初始投资另列且不重复计入周期成本。

距离矩阵、行驶时长与时间精度按每个案例原输入记录；本研究采用原日程、窗口和需求。费用系数为同一人工配置，等待计入占用工时，趟间闲置薪酬不计。车辆按唯一物理车辆计费，趟次按实际路线计费。候选时段、释放假设、允许批次和所有价格均在 inputs 快照中。

复现命令：

    .venv/bin/python -m experiments.runs.run_model6_comparison --manifest data/model6/static_v2_lade_20261004/manifest.json --exact-time 15 --time 8 --iterations 30000 --seeds 42 43 44 --treatments M5_distance M6_pool_only M6_joint --alns-ablations

逐次运行的原始路线、独立审计、成本分项在各运行目录；rows.csv 保留失败及超时；experiment.json 包含预算、完整输入和源码哈希。
