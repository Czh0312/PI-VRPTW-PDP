# 原算例一级容量改为50：M3/M8 ALNS重跑

只将 data/mechanism_l05_3sat_balanced_v1/fleet.json 的 E1_Ck 从200改为50；重量容量仍为3000。顾客、分仓、时间窗、需求、费用、清洗、车辆数及算法代码均不变。此前单独创建的1000重量容量候选未用于本次运行。
种子17、42、2026，每次ALNS预算60秒、迭代上限10亿。M8冷启动和同一历史可行见证启动分开报告；不新增固定K对照。

| 运行方式 | 可行/次数 | 已证不可行次数 | 平均N | 平均K | 平均成本 | 一级出仓体积装载率 |
|---|---:|---:|---:|---:|---:|---:|
| M3_cold | 0/3 | 3 | — | — | — | — |
| M8_cold | 0/3 | 0 | — | — | — | — |
| M8_feasible_start | 3/3 | 0 | 35.00 | 3.00 | 408.73 | 23.15% |

M3若触发UNSPLIT_E1_DELIVERY_CAPACITY，是S3单批54箱超过50箱，当前模型不允许同仓同批由多车拆分配送。增加车数不能解除该约束；这是输入必要条件证明，不是ALNS搜索失败。
M8的HEURISTIC_NO_SOLUTION表示预算内未找到可行解，不能判为不可行。全部可行解重算共同成本，并独立审计与连续两周期重放。
M3若不可行，本次不能给出同口径的M3/M8成本节约率，也不能将模型可行性差异直接解释为经济优势。
当前目录保存逐次结果、初解、输入/源码快照、比较费用、检查错误和哈希。原200容量历史结果仍保留于旧输出目录；原数据目录中的历史input_checks/design属于此前200容量记录，本次有效检查以当前输出为准。

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run
- Origin Date: 2026-10-20
- Verification Status: ANALYZED; no reproducibility rerun
- Version Label: l05_capacity50_rerun

装载率均值口径：先计算各种子内实际配送趟次的平均出仓装载率，再对三个种子取平均。M8体积装载率23.15%，重量装载率6.98%；全部26趟合并的体积装载率为23.08%。容量分母缩小不等于运输效率同比提升。
