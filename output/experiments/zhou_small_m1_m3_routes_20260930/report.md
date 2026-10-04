# 单批次 M1–M4 全量精确求解

Material Passport: Origin Skill = academic-research-suite / experiment-agent; Mode = run; Verification Status = EXECUTED.

进度：10/10；状态：COMPLETED。
每项运行保留原始状态、可行方案、上下界、证明进度和独立审计；失败及超时不重试、不剔除。

| 模型 | 已完成 | 已证明最优 | 可行未证最优 | 已证明不可行 | 时限内无解 | 异常/审计失败 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| M1 | 5 | 5 | 0 | 0 | 0 | 0 |
| M3 | 5 | 5 | 0 | 0 | 0 | 0 |

## 预算与结果解释

每个案例×模型预算为 120 秒，种子 42。预算包含建模时间；外层额外时间见 manifest.json。
M2/M4 的预算覆盖整个分仓枚举，不能把一个子问题最优当作全局最优。
时限内没有可行解不等于证明不可行。有可行解的结果均经过独立审计和两周期重放。
现有派生情景的最优路线不代表完整真实经营数据或经济性结论。

[全部运行表](runs.csv) · [运行清单与冻结参数](manifest.json) · [进度](progress.json)

## 各案例结果

| 案例 | 模型 | 状态 | 距离 | 下界 | Gap | 分仓证明（已闭合/总数） |
| --- | --- | --- | ---: | ---: | ---: | --- |
| zhou_Set1_7-2-1_b1 | M1 | [OPTIMAL](zhou_Set1_7-2-1_b1_M1_exact_seed42/report.md) | 295.160365 | 295.160365 | 0.00% | — |
| zhou_Set1_7-2-1_b1 | M3 | [OPTIMAL](zhou_Set1_7-2-1_b1_M3_exact_seed42/report.md) | 299.143031 | 299.143031 | 0.00% | — |
| zhou_Set1_7-2-1b_b1 | M1 | [OPTIMAL](zhou_Set1_7-2-1b_b1_M1_exact_seed42/report.md) | 247.664216 | 247.664216 | 0.00% | — |
| zhou_Set1_7-2-1b_b1 | M3 | [OPTIMAL](zhou_Set1_7-2-1b_b1_M3_exact_seed42/report.md) | 298.459566 | 298.459566 | 0.00% | — |
| zhou_Set1_7-2-2_b1 | M1 | [OPTIMAL](zhou_Set1_7-2-2_b1_M1_exact_seed42/report.md) | 237.864378 | 237.864378 | 0.00% | — |
| zhou_Set1_7-2-2_b1 | M3 | [OPTIMAL](zhou_Set1_7-2-2_b1_M3_exact_seed42/report.md) | 264.925338 | 264.925338 | 0.00% | — |
| zhou_Set1_7-2-2b_b1 | M1 | [OPTIMAL](zhou_Set1_7-2-2b_b1_M1_exact_seed42/report.md) | 224.615772 | 224.615772 | 0.00% | — |
| zhou_Set1_7-2-2b_b1 | M3 | [OPTIMAL](zhou_Set1_7-2-2b_b1_M3_exact_seed42/report.md) | 232.048412 | 232.048412 | 0.00% | — |
| zhou_Set1_7-2-3_b1 | M1 | [OPTIMAL](zhou_Set1_7-2-3_b1_M1_exact_seed42/report.md) | 224.864044 | 224.864044 | 0.00% | — |
| zhou_Set1_7-2-3_b1 | M3 | [OPTIMAL](zhou_Set1_7-2-3_b1_M3_exact_seed42/report.md) | 253.709397 | 253.709397 | 0.00% | — |
