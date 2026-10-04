# 单批次 M1–M4 全量精确求解

Material Passport: Origin Skill = academic-research-suite / experiment-agent; Mode = run; Verification Status = IN_PROGRESS.

进度：171/276；状态：PAUSED。
每项运行保留原始状态、可行方案、上下界、证明进度和独立审计；失败及超时不重试、不剔除。

| 模型 | 已完成 | 已证明最优 | 可行未证最优 | 已证明不可行 | 时限内无解 | 异常/审计失败 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| M1 | 43 | 38 | 5 | 0 | 0 | 0 |
| M2 | 43 | 8 | 35 | 0 | 0 | 0 |
| M3 | 43 | 34 | 5 | 0 | 4 | 0 |
| M4 | 42 | 8 | 25 | 0 | 9 | 0 |

## 预算与结果解释

每个案例×模型预算为 300 秒，种子 42。预算包含建模时间；外层额外时间见 manifest.json。
M2/M4 的预算覆盖整个分仓枚举，不能把一个子问题最优当作全局最优。
时限内没有可行解不等于证明不可行。有可行解的结果均经过独立审计和两周期重放。
现有派生情景的最优路线不代表完整真实经营数据或经济性结论。

[全部运行表](runs.csv) · [运行清单与冻结参数](manifest.json) · [进度](progress.json)

## 各案例结果

| 案例 | 模型 | 状态 | 距离 | 下界 | Gap | 分仓证明（已闭合/总数） |
| --- | --- | --- | ---: | ---: | ---: | --- |
| mechanism_cutoff_choice_b1 | M1 | [OPTIMAL](mechanism_cutoff_choice_b1_M1_exact_seed42/report.md) | 17.656854 | 17.656854 | 0.00% | — |
| mechanism_cutoff_choice_b1 | M2 | [OPTIMAL](mechanism_cutoff_choice_b1_M2_exact_seed42/report.md) | 17.656854 | 17.656854 | 0.00% | 4/4 |
| mechanism_cutoff_choice_b1 | M3 | [OPTIMAL](mechanism_cutoff_choice_b1_M3_exact_seed42/report.md) | 20.000000 | 20.000000 | 0.00% | — |
| mechanism_cutoff_choice_b1 | M4 | [OPTIMAL](mechanism_cutoff_choice_b1_M4_exact_seed42/report.md) | 20.000000 | 20.000000 | 0.00% | 4/4 |
| mechanism_forced_late_b1 | M1 | [OPTIMAL](mechanism_forced_late_b1_M1_exact_seed42/report.md) | 17.656854 | 17.656854 | 0.00% | — |
| mechanism_forced_late_b1 | M2 | [OPTIMAL](mechanism_forced_late_b1_M2_exact_seed42/report.md) | 17.656854 | 17.656854 | 0.00% | 4/4 |
| mechanism_forced_late_b1 | M3 | [OPTIMAL](mechanism_forced_late_b1_M3_exact_seed42/report.md) | 20.000000 | 20.000000 | 0.00% | — |
| mechanism_forced_late_b1 | M4 | [OPTIMAL](mechanism_forced_late_b1_M4_exact_seed42/report.md) | 20.000000 | 20.000000 | 0.00% | 4/4 |
| zhou_Set1_7-2-1_b1 | M1 | [OPTIMAL](zhou_Set1_7-2-1_b1_M1_exact_seed42/report.md) | 295.160365 | 295.160365 | 0.00% | — |
| zhou_Set1_7-2-1_b1 | M2 | [OPTIMAL](zhou_Set1_7-2-1_b1_M2_exact_seed42/report.md) | 217.124175 | 217.124175 | 0.00% | 128/128 |
| zhou_Set1_7-2-1_b1 | M3 | [OPTIMAL](zhou_Set1_7-2-1_b1_M3_exact_seed42/report.md) | 299.143031 | 299.143031 | 0.00% | — |
| zhou_Set1_7-2-1_b1 | M4 | [OPTIMAL](zhou_Set1_7-2-1_b1_M4_exact_seed42/report.md) | 249.435123 | 249.435123 | 0.00% | 128/128 |
| zhou_Set1_7-2-1b_b1 | M1 | [OPTIMAL](zhou_Set1_7-2-1b_b1_M1_exact_seed42/report.md) | 247.664216 | 247.664216 | 0.00% | — |
| zhou_Set1_7-2-1b_b1 | M2 | [OPTIMAL](zhou_Set1_7-2-1b_b1_M2_exact_seed42/report.md) | 216.154888 | 216.154888 | 0.00% | 128/128 |
| zhou_Set1_7-2-1b_b1 | M3 | [OPTIMAL](zhou_Set1_7-2-1b_b1_M3_exact_seed42/report.md) | 298.459566 | 298.459566 | 0.00% | — |
| zhou_Set1_7-2-1b_b1 | M4 | [OPTIMAL](zhou_Set1_7-2-1b_b1_M4_exact_seed42/report.md) | 244.784968 | 244.784968 | 0.00% | 128/128 |
| zhou_Set1_7-2-2_b1 | M1 | [OPTIMAL](zhou_Set1_7-2-2_b1_M1_exact_seed42/report.md) | 237.864378 | 237.864378 | 0.00% | — |
| zhou_Set1_7-2-2_b1 | M2 | [OPTIMAL](zhou_Set1_7-2-2_b1_M2_exact_seed42/report.md) | 161.391357 | 161.391357 | 0.00% | 128/128 |
| zhou_Set1_7-2-2_b1 | M3 | [OPTIMAL](zhou_Set1_7-2-2_b1_M3_exact_seed42/report.md) | 264.925338 | 264.925338 | 0.00% | — |
| zhou_Set1_7-2-2_b1 | M4 | [OPTIMAL](zhou_Set1_7-2-2_b1_M4_exact_seed42/report.md) | 187.436300 | 187.436300 | 0.00% | 128/128 |
| zhou_Set1_7-2-2b_b1 | M1 | [OPTIMAL](zhou_Set1_7-2-2b_b1_M1_exact_seed42/report.md) | 224.615772 | 224.615772 | 0.00% | — |
| zhou_Set1_7-2-2b_b1 | M2 | [OPTIMAL](zhou_Set1_7-2-2b_b1_M2_exact_seed42/report.md) | 187.104762 | 187.104762 | 0.00% | 128/128 |
| zhou_Set1_7-2-2b_b1 | M3 | [OPTIMAL](zhou_Set1_7-2-2b_b1_M3_exact_seed42/report.md) | 232.048412 | 232.048412 | 0.00% | — |
| zhou_Set1_7-2-2b_b1 | M4 | [OPTIMAL](zhou_Set1_7-2-2b_b1_M4_exact_seed42/report.md) | 196.933123 | 196.933123 | 0.00% | 128/128 |
| zhou_Set1_7-2-3_b1 | M1 | [OPTIMAL](zhou_Set1_7-2-3_b1_M1_exact_seed42/report.md) | 224.864044 | 224.864044 | 0.00% | — |
| zhou_Set1_7-2-3_b1 | M2 | [OPTIMAL](zhou_Set1_7-2-3_b1_M2_exact_seed42/report.md) | 200.464843 | 200.464843 | 0.00% | 128/128 |
| zhou_Set1_7-2-3_b1 | M3 | [OPTIMAL](zhou_Set1_7-2-3_b1_M3_exact_seed42/report.md) | 253.709397 | 253.709397 | 0.00% | — |
| zhou_Set1_7-2-3_b1 | M4 | [OPTIMAL](zhou_Set1_7-2-3_b1_M4_exact_seed42/report.md) | 220.551890 | 220.551890 | 0.00% | 128/128 |
| zhou_Set1_7-2-3b_b1 | M1 | [OPTIMAL](zhou_Set1_7-2-3b_b1_M1_exact_seed42/report.md) | 195.540338 | 195.540338 | 0.00% | — |
| zhou_Set1_7-2-3b_b1 | M2 | [OPTIMAL](zhou_Set1_7-2-3b_b1_M2_exact_seed42/report.md) | 195.540338 | 195.540338 | 0.00% | 128/128 |
| zhou_Set1_7-2-3b_b1 | M3 | [OPTIMAL](zhou_Set1_7-2-3b_b1_M3_exact_seed42/report.md) | 212.732243 | 212.732243 | 0.00% | — |
| zhou_Set1_7-2-3b_b1 | M4 | [OPTIMAL](zhou_Set1_7-2-3b_b1_M4_exact_seed42/report.md) | 212.732243 | 212.732243 | 0.00% | 128/128 |
| lade_0823_10aoi_b1 | M1 | [OPTIMAL](lade_0823_10aoi_b1_M1_exact_seed42/report.md) | 92.634100 | 92.634100 | 0.00% | — |
| lade_0823_10aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0823_10aoi_b1_M2_exact_seed42/report.md) | 87.399300 | -0.000000 | 100.00% | 279/59049 |
| lade_0823_10aoi_b1 | M3 | [OPTIMAL](lade_0823_10aoi_b1_M3_exact_seed42/report.md) | 101.945300 | 101.945300 | 0.00% | — |
| lade_0823_10aoi_b1 | M4 | [FEASIBLE_NOT_PROVEN](lade_0823_10aoi_b1_M4_exact_seed42/report.md) | 100.571600 | 0.000000 | 100.00% | 29/59049 |
| lade_0824_10aoi_b1 | M1 | [OPTIMAL](lade_0824_10aoi_b1_M1_exact_seed42/report.md) | 92.634100 | 92.634100 | 0.00% | — |
| lade_0824_10aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0824_10aoi_b1_M2_exact_seed42/report.md) | 87.399300 | 0.000000 | 100.00% | 284/59049 |
| lade_0824_10aoi_b1 | M3 | [OPTIMAL](lade_0824_10aoi_b1_M3_exact_seed42/report.md) | 102.489300 | 102.489300 | 0.00% | — |
| lade_0824_10aoi_b1 | M4 | [FEASIBLE_NOT_PROVEN](lade_0824_10aoi_b1_M4_exact_seed42/report.md) | 96.120600 | 0.000000 | 100.00% | 19/59049 |
| lade_0825_10aoi_b1 | M1 | [OPTIMAL](lade_0825_10aoi_b1_M1_exact_seed42/report.md) | 92.634100 | 92.634100 | 0.00% | — |
| lade_0825_10aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0825_10aoi_b1_M2_exact_seed42/report.md) | 87.399300 | -0.000000 | 100.00% | 299/59049 |
| lade_0825_10aoi_b1 | M3 | [OPTIMAL](lade_0825_10aoi_b1_M3_exact_seed42/report.md) | 101.939900 | 101.939900 | 0.00% | — |
| lade_0825_10aoi_b1 | M4 | [FEASIBLE_NOT_PROVEN](lade_0825_10aoi_b1_M4_exact_seed42/report.md) | 96.348500 | 0.000000 | 100.00% | 38/59049 |
| lade_0826_10aoi_b1 | M1 | [OPTIMAL](lade_0826_10aoi_b1_M1_exact_seed42/report.md) | 92.634100 | 92.634100 | 0.00% | — |
| lade_0826_10aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0826_10aoi_b1_M2_exact_seed42/report.md) | 87.399300 | -0.000000 | 100.00% | 300/59049 |
| lade_0826_10aoi_b1 | M3 | [OPTIMAL](lade_0826_10aoi_b1_M3_exact_seed42/report.md) | 101.670400 | 101.670400 | 0.00% | — |
| lade_0826_10aoi_b1 | M4 | [FEASIBLE_NOT_PROVEN](lade_0826_10aoi_b1_M4_exact_seed42/report.md) | 100.316600 | 0.000000 | 100.00% | 29/59049 |
| lade_0827_10aoi_b1 | M1 | [OPTIMAL](lade_0827_10aoi_b1_M1_exact_seed42/report.md) | 92.634100 | 92.634100 | 0.00% | — |
| lade_0827_10aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0827_10aoi_b1_M2_exact_seed42/report.md) | 87.399300 | -0.000000 | 100.00% | 295/59049 |
| lade_0827_10aoi_b1 | M3 | [OPTIMAL](lade_0827_10aoi_b1_M3_exact_seed42/report.md) | 101.204700 | 101.204700 | 0.00% | — |
| lade_0827_10aoi_b1 | M4 | [FEASIBLE_NOT_PROVEN](lade_0827_10aoi_b1_M4_exact_seed42/report.md) | 97.088800 | 0.000000 | 100.00% | 54/59049 |
| lade_0828_10aoi_b1 | M1 | [OPTIMAL](lade_0828_10aoi_b1_M1_exact_seed42/report.md) | 92.634100 | 92.634100 | 0.00% | — |
| lade_0828_10aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0828_10aoi_b1_M2_exact_seed42/report.md) | 87.399300 | -0.000000 | 100.00% | 302/59049 |
| lade_0828_10aoi_b1 | M3 | [OPTIMAL](lade_0828_10aoi_b1_M3_exact_seed42/report.md) | 101.234000 | 101.234000 | 0.00% | — |
| lade_0828_10aoi_b1 | M4 | [FEASIBLE_NOT_PROVEN](lade_0828_10aoi_b1_M4_exact_seed42/report.md) | 99.513500 | 0.000000 | 100.00% | 25/59049 |
| lade_0829_10aoi_b1 | M1 | [OPTIMAL](lade_0829_10aoi_b1_M1_exact_seed42/report.md) | 92.634100 | 92.634100 | 0.00% | — |
| lade_0829_10aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0829_10aoi_b1_M2_exact_seed42/report.md) | 87.399300 | -0.000000 | 100.00% | 300/59049 |
| lade_0829_10aoi_b1 | M3 | [OPTIMAL](lade_0829_10aoi_b1_M3_exact_seed42/report.md) | 101.132900 | 101.132900 | 0.00% | — |
| lade_0829_10aoi_b1 | M4 | [FEASIBLE_NOT_PROVEN](lade_0829_10aoi_b1_M4_exact_seed42/report.md) | 98.745900 | 0.000000 | 100.00% | 58/59049 |
| zhou_Set1_10-2-1_b1 | M1 | [OPTIMAL](zhou_Set1_10-2-1_b1_M1_exact_seed42/report.md) | 205.448205 | 205.448205 | 0.00% | — |
| zhou_Set1_10-2-1_b1 | M2 | [FEASIBLE_NOT_PROVEN](zhou_Set1_10-2-1_b1_M2_exact_seed42/report.md) | 205.448205 | 0.000000 | 100.00% | 142/1024 |
| zhou_Set1_10-2-1_b1 | M3 | [OPTIMAL](zhou_Set1_10-2-1_b1_M3_exact_seed42/report.md) | 241.021415 | 241.021415 | 0.00% | — |
| zhou_Set1_10-2-1_b1 | M4 | [FEASIBLE_NOT_PROVEN](zhou_Set1_10-2-1_b1_M4_exact_seed42/report.md) | 241.021415 | 0.000000 | 100.00% | 46/1024 |
| zhou_Set1_10-2-1b_b1 | M1 | [OPTIMAL](zhou_Set1_10-2-1b_b1_M1_exact_seed42/report.md) | 355.977371 | 355.977371 | 0.00% | — |
| zhou_Set1_10-2-1b_b1 | M2 | [FEASIBLE_NOT_PROVEN](zhou_Set1_10-2-1b_b1_M2_exact_seed42/report.md) | 298.387063 | 0.000000 | 100.00% | 168/1024 |
| zhou_Set1_10-2-1b_b1 | M3 | [OPTIMAL](zhou_Set1_10-2-1b_b1_M3_exact_seed42/report.md) | 378.465204 | 378.465204 | 0.00% | — |
| zhou_Set1_10-2-1b_b1 | M4 | [FEASIBLE_NOT_PROVEN](zhou_Set1_10-2-1b_b1_M4_exact_seed42/report.md) | 341.822725 | 0.000000 | 100.00% | 65/1024 |
| zhou_Set1_10-2-2_b1 | M1 | [OPTIMAL](zhou_Set1_10-2-2_b1_M1_exact_seed42/report.md) | 278.885410 | 278.885410 | 0.00% | — |
| zhou_Set1_10-2-2_b1 | M2 | [FEASIBLE_NOT_PROVEN](zhou_Set1_10-2-2_b1_M2_exact_seed42/report.md) | 277.812474 | 0.000000 | 100.00% | 133/1024 |
| zhou_Set1_10-2-2_b1 | M3 | [OPTIMAL](zhou_Set1_10-2-2_b1_M3_exact_seed42/report.md) | 321.657317 | 321.657317 | 0.00% | — |
| zhou_Set1_10-2-2_b1 | M4 | [FEASIBLE_NOT_PROVEN](zhou_Set1_10-2-2_b1_M4_exact_seed42/report.md) | 311.736955 | 0.000000 | 100.00% | 58/1024 |
| zhou_Set1_10-2-2b_b1 | M1 | [OPTIMAL](zhou_Set1_10-2-2b_b1_M1_exact_seed42/report.md) | 253.309597 | 253.309597 | 0.00% | — |
| zhou_Set1_10-2-2b_b1 | M2 | [FEASIBLE_NOT_PROVEN](zhou_Set1_10-2-2b_b1_M2_exact_seed42/report.md) | 239.765836 | 0.000000 | 100.00% | 106/1024 |
| zhou_Set1_10-2-2b_b1 | M3 | [OPTIMAL](zhou_Set1_10-2-2b_b1_M3_exact_seed42/report.md) | 287.801657 | 287.801657 | 0.00% | — |
| zhou_Set1_10-2-2b_b1 | M4 | [FEASIBLE_NOT_PROVEN](zhou_Set1_10-2-2b_b1_M4_exact_seed42/report.md) | 253.844008 | 0.000000 | 100.00% | 19/1024 |
| zhou_Set1_10-2-3_b1 | M1 | [OPTIMAL](zhou_Set1_10-2-3_b1_M1_exact_seed42/report.md) | 273.882461 | 273.882461 | 0.00% | — |
| zhou_Set1_10-2-3_b1 | M2 | [FEASIBLE_NOT_PROVEN](zhou_Set1_10-2-3_b1_M2_exact_seed42/report.md) | 251.588973 | 0.000000 | 100.00% | 163/1024 |
| zhou_Set1_10-2-3_b1 | M3 | [OPTIMAL](zhou_Set1_10-2-3_b1_M3_exact_seed42/report.md) | 286.582940 | 286.582940 | 0.00% | — |
| zhou_Set1_10-2-3_b1 | M4 | [FEASIBLE_NOT_PROVEN](zhou_Set1_10-2-3_b1_M4_exact_seed42/report.md) | 286.582940 | 0.000000 | 100.00% | 60/1024 |
| zhou_Set1_10-2-3b_b1 | M1 | [OPTIMAL](zhou_Set1_10-2-3b_b1_M1_exact_seed42/report.md) | 265.694047 | 265.694047 | 0.00% | — |
| zhou_Set1_10-2-3b_b1 | M2 | [FEASIBLE_NOT_PROVEN](zhou_Set1_10-2-3b_b1_M2_exact_seed42/report.md) | 254.681302 | 0.000000 | 100.00% | 125/1024 |
| zhou_Set1_10-2-3b_b1 | M3 | [OPTIMAL](zhou_Set1_10-2-3b_b1_M3_exact_seed42/report.md) | 317.276279 | 317.276279 | 0.00% | — |
| zhou_Set1_10-2-3b_b1 | M4 | [FEASIBLE_NOT_PROVEN](zhou_Set1_10-2-3b_b1_M4_exact_seed42/report.md) | 317.276279 | 0.000000 | 100.00% | 56/1024 |
| zhou_Set1_10-2-4_b1 | M1 | [OPTIMAL](zhou_Set1_10-2-4_b1_M1_exact_seed42/report.md) | 256.314741 | 256.314741 | 0.00% | — |
| zhou_Set1_10-2-4_b1 | M2 | [FEASIBLE_NOT_PROVEN](zhou_Set1_10-2-4_b1_M2_exact_seed42/report.md) | 246.325315 | 0.000000 | 100.00% | 119/1024 |
| zhou_Set1_10-2-4_b1 | M3 | [OPTIMAL](zhou_Set1_10-2-4_b1_M3_exact_seed42/report.md) | 275.158746 | 275.158746 | 0.00% | — |
| zhou_Set1_10-2-4_b1 | M4 | [FEASIBLE_NOT_PROVEN](zhou_Set1_10-2-4_b1_M4_exact_seed42/report.md) | 272.349906 | 0.000000 | 100.00% | 27/1024 |
| zhou_Set1_10-2-5_b1 | M1 | [OPTIMAL](zhou_Set1_10-2-5_b1_M1_exact_seed42/report.md) | 316.742570 | 316.742570 | 0.00% | — |
| zhou_Set1_10-2-5_b1 | M2 | [FEASIBLE_NOT_PROVEN](zhou_Set1_10-2-5_b1_M2_exact_seed42/report.md) | 302.251322 | 0.000000 | 100.00% | 147/1024 |
| zhou_Set1_10-2-5_b1 | M3 | [OPTIMAL](zhou_Set1_10-2-5_b1_M3_exact_seed42/report.md) | 357.374489 | 357.374489 | 0.00% | — |
| zhou_Set1_10-2-5_b1 | M4 | [FEASIBLE_NOT_PROVEN](zhou_Set1_10-2-5_b1_M4_exact_seed42/report.md) | 357.207263 | 0.000000 | 100.00% | 48/1024 |
| zhou_Set1_12-2-1_b1 | M1 | [OPTIMAL](zhou_Set1_12-2-1_b1_M1_exact_seed42/report.md) | 306.789689 | 306.789689 | 0.00% | — |
| zhou_Set1_12-2-1_b1 | M2 | [FEASIBLE_NOT_PROVEN](zhou_Set1_12-2-1_b1_M2_exact_seed42/report.md) | 306.789689 | 0.000000 | 100.00% | 60/4096 |
| zhou_Set1_12-2-1_b1 | M3 | [OPTIMAL](zhou_Set1_12-2-1_b1_M3_exact_seed42/report.md) | 339.113875 | 339.113875 | 0.00% | — |
| zhou_Set1_12-2-1_b1 | M4 | [FEASIBLE_NOT_PROVEN](zhou_Set1_12-2-1_b1_M4_exact_seed42/report.md) | 339.113875 | 0.000000 | 100.00% | 23/4096 |
| zhou_Set1_12-2-1b_b1 | M1 | [OPTIMAL](zhou_Set1_12-2-1b_b1_M1_exact_seed42/report.md) | 309.287558 | 309.287558 | 0.00% | — |
| zhou_Set1_12-2-1b_b1 | M2 | [FEASIBLE_NOT_PROVEN](zhou_Set1_12-2-1b_b1_M2_exact_seed42/report.md) | 309.287558 | 0.000000 | 100.00% | 69/4096 |
| zhou_Set1_12-2-1b_b1 | M3 | [OPTIMAL](zhou_Set1_12-2-1b_b1_M3_exact_seed42/report.md) | 388.391078 | 388.391078 | 0.00% | — |
| zhou_Set1_12-2-1b_b1 | M4 | [FEASIBLE_NOT_PROVEN](zhou_Set1_12-2-1b_b1_M4_exact_seed42/report.md) | 388.391078 | 0.000000 | 100.00% | 20/4096 |
| zhou_Set1_12-2-2_b1 | M1 | [OPTIMAL](zhou_Set1_12-2-2_b1_M1_exact_seed42/report.md) | 272.617551 | 272.617551 | 0.00% | — |
| zhou_Set1_12-2-2_b1 | M2 | [FEASIBLE_NOT_PROVEN](zhou_Set1_12-2-2_b1_M2_exact_seed42/report.md) | 250.134427 | 0.000000 | 100.00% | 52/4096 |
| zhou_Set1_12-2-2_b1 | M3 | [OPTIMAL](zhou_Set1_12-2-2_b1_M3_exact_seed42/report.md) | 292.992318 | 292.992318 | 0.00% | — |
| zhou_Set1_12-2-2_b1 | M4 | [FEASIBLE_NOT_PROVEN](zhou_Set1_12-2-2_b1_M4_exact_seed42/report.md) | 266.164583 | 0.000000 | 100.00% | 19/4096 |
| zhou_Set1_12-2-2b_b1 | M1 | [OPTIMAL](zhou_Set1_12-2-2b_b1_M1_exact_seed42/report.md) | 341.606822 | 341.606822 | 0.00% | — |
| zhou_Set1_12-2-2b_b1 | M2 | [FEASIBLE_NOT_PROVEN](zhou_Set1_12-2-2b_b1_M2_exact_seed42/report.md) | 322.393199 | 0.000000 | 100.00% | 53/4096 |
| zhou_Set1_12-2-2b_b1 | M3 | [OPTIMAL](zhou_Set1_12-2-2b_b1_M3_exact_seed42/report.md) | 353.176336 | 353.176336 | 0.00% | — |
| zhou_Set1_12-2-2b_b1 | M4 | [FEASIBLE_NOT_PROVEN](zhou_Set1_12-2-2b_b1_M4_exact_seed42/report.md) | 344.893239 | 0.000000 | 100.00% | 17/4096 |
| zhou_Set1_12-2-3_b1 | M1 | [OPTIMAL](zhou_Set1_12-2-3_b1_M1_exact_seed42/report.md) | 298.424656 | 298.424656 | 0.00% | — |
| zhou_Set1_12-2-3_b1 | M2 | [FEASIBLE_NOT_PROVEN](zhou_Set1_12-2-3_b1_M2_exact_seed42/report.md) | 294.028447 | 0.000000 | 100.00% | 38/4096 |
| zhou_Set1_12-2-3_b1 | M3 | [OPTIMAL](zhou_Set1_12-2-3_b1_M3_exact_seed42/report.md) | 364.613240 | 364.613240 | 0.00% | — |
| zhou_Set1_12-2-3_b1 | M4 | [FEASIBLE_NOT_PROVEN](zhou_Set1_12-2-3_b1_M4_exact_seed42/report.md) | 360.707263 | 0.000000 | 100.00% | 19/4096 |
| zhou_Set1_12-2-3b_b1 | M1 | [OPTIMAL](zhou_Set1_12-2-3b_b1_M1_exact_seed42/report.md) | 331.112330 | 331.112330 | 0.00% | — |
| zhou_Set1_12-2-3b_b1 | M2 | [FEASIBLE_NOT_PROVEN](zhou_Set1_12-2-3b_b1_M2_exact_seed42/report.md) | 324.410906 | 0.000000 | 100.00% | 53/4096 |
| zhou_Set1_12-2-3b_b1 | M3 | [OPTIMAL](zhou_Set1_12-2-3b_b1_M3_exact_seed42/report.md) | 375.117555 | 375.117555 | 0.00% | — |
| zhou_Set1_12-2-3b_b1 | M4 | [FEASIBLE_NOT_PROVEN](zhou_Set1_12-2-3b_b1_M4_exact_seed42/report.md) | 370.225458 | 0.000000 | 100.00% | 33/4096 |
| zhou_Set1_12-2-4_b1 | M1 | [OPTIMAL](zhou_Set1_12-2-4_b1_M1_exact_seed42/report.md) | 323.879576 | 323.879576 | 0.00% | — |
| zhou_Set1_12-2-4_b1 | M2 | [FEASIBLE_NOT_PROVEN](zhou_Set1_12-2-4_b1_M2_exact_seed42/report.md) | 320.167207 | 0.000000 | 100.00% | 77/4096 |
| zhou_Set1_12-2-4_b1 | M3 | [OPTIMAL](zhou_Set1_12-2-4_b1_M3_exact_seed42/report.md) | 367.245825 | 367.245825 | 0.00% | — |
| zhou_Set1_12-2-4_b1 | M4 | [FEASIBLE_NOT_PROVEN](zhou_Set1_12-2-4_b1_M4_exact_seed42/report.md) | 353.452070 | 0.000000 | 100.00% | 38/4096 |
| zhou_Set1_12-2-5_b1 | M1 | [OPTIMAL](zhou_Set1_12-2-5_b1_M1_exact_seed42/report.md) | 292.005039 | 292.005039 | 0.00% | — |
| zhou_Set1_12-2-5_b1 | M2 | [FEASIBLE_NOT_PROVEN](zhou_Set1_12-2-5_b1_M2_exact_seed42/report.md) | 278.983591 | 0.000000 | 100.00% | 93/4096 |
| zhou_Set1_12-2-5_b1 | M3 | [OPTIMAL](zhou_Set1_12-2-5_b1_M3_exact_seed42/report.md) | 322.793187 | 322.793187 | 0.00% | — |
| zhou_Set1_12-2-5_b1 | M4 | [FEASIBLE_NOT_PROVEN](zhou_Set1_12-2-5_b1_M4_exact_seed42/report.md) | 322.793187 | 0.000000 | 100.00% | 41/4096 |
| lade_0823_20aoi_b1 | M1 | [OPTIMAL](lade_0823_20aoi_b1_M1_exact_seed42/report.md) | 99.617700 | 99.617700 | 0.00% | — |
| lade_0823_20aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0823_20aoi_b1_M2_exact_seed42/report.md) | 92.104700 | 0.000000 | 100.00% | 5/3486784401 |
| lade_0823_20aoi_b1 | M3 | [FEASIBLE_NOT_PROVEN](lade_0823_20aoi_b1_M3_exact_seed42/report.md) | 124.569300 | 120.170764 | 3.53% | — |
| lade_0823_20aoi_b1 | M4 | [FEASIBLE_NOT_PROVEN](lade_0823_20aoi_b1_M4_exact_seed42/report.md) | 143.716400 | 0.000000 | 100.00% | 0/3486784401 |
| lade_0824_20aoi_b1 | M1 | [OPTIMAL](lade_0824_20aoi_b1_M1_exact_seed42/report.md) | 99.617700 | 99.617700 | 0.00% | — |
| lade_0824_20aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0824_20aoi_b1_M2_exact_seed42/report.md) | 92.104700 | 0.000000 | 100.00% | 16/3486784401 |
| lade_0824_20aoi_b1 | M3 | [OPTIMAL](lade_0824_20aoi_b1_M3_exact_seed42/report.md) | 121.833900 | 121.833900 | 0.00% | — |
| lade_0824_20aoi_b1 | M4 | [NO_SOLUTION_WITHIN_BUDGET](lade_0824_20aoi_b1_M4_exact_seed42/report.md) | — | 0.000000 | — | 0/3486784401 |
| lade_0825_20aoi_b1 | M1 | [OPTIMAL](lade_0825_20aoi_b1_M1_exact_seed42/report.md) | 99.617697 | 99.617697 | 0.00% | — |
| lade_0825_20aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0825_20aoi_b1_M2_exact_seed42/report.md) | 92.104700 | 0.000000 | 100.00% | 7/3486784401 |
| lade_0825_20aoi_b1 | M3 | [OPTIMAL](lade_0825_20aoi_b1_M3_exact_seed42/report.md) | 116.424000 | 116.424000 | 0.00% | — |
| lade_0825_20aoi_b1 | M4 | [FEASIBLE_NOT_PROVEN](lade_0825_20aoi_b1_M4_exact_seed42/report.md) | 121.479500 | 0.000000 | 100.00% | 0/3486784401 |
| lade_0826_20aoi_b1 | M1 | [OPTIMAL](lade_0826_20aoi_b1_M1_exact_seed42/report.md) | 99.617698 | 99.617698 | 0.00% | — |
| lade_0826_20aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0826_20aoi_b1_M2_exact_seed42/report.md) | 92.104700 | 0.000000 | 100.00% | 5/3486784401 |
| lade_0826_20aoi_b1 | M3 | [FEASIBLE_NOT_PROVEN](lade_0826_20aoi_b1_M3_exact_seed42/report.md) | 122.165000 | 119.684589 | 2.03% | — |
| lade_0826_20aoi_b1 | M4 | [NO_SOLUTION_WITHIN_BUDGET](lade_0826_20aoi_b1_M4_exact_seed42/report.md) | — | 0.000000 | — | 0/3486784401 |
| lade_0827_20aoi_b1 | M1 | [OPTIMAL](lade_0827_20aoi_b1_M1_exact_seed42/report.md) | 99.617700 | 99.617700 | 0.00% | — |
| lade_0827_20aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0827_20aoi_b1_M2_exact_seed42/report.md) | 92.104700 | 0.000000 | 100.00% | 6/3486784401 |
| lade_0827_20aoi_b1 | M3 | [OPTIMAL](lade_0827_20aoi_b1_M3_exact_seed42/report.md) | 120.306400 | 120.306400 | 0.00% | — |
| lade_0827_20aoi_b1 | M4 | [NO_SOLUTION_WITHIN_BUDGET](lade_0827_20aoi_b1_M4_exact_seed42/report.md) | — | 0.000000 | — | 0/3486784401 |
| lade_0828_20aoi_b1 | M1 | [OPTIMAL](lade_0828_20aoi_b1_M1_exact_seed42/report.md) | 99.617700 | 99.617700 | 0.00% | — |
| lade_0828_20aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0828_20aoi_b1_M2_exact_seed42/report.md) | 92.104700 | 0.000000 | 100.00% | 5/3486784401 |
| lade_0828_20aoi_b1 | M3 | [FEASIBLE_NOT_PROVEN](lade_0828_20aoi_b1_M3_exact_seed42/report.md) | 117.041300 | 113.413034 | 3.10% | — |
| lade_0828_20aoi_b1 | M4 | [NO_SOLUTION_WITHIN_BUDGET](lade_0828_20aoi_b1_M4_exact_seed42/report.md) | — | 0.000000 | — | 0/3486784401 |
| lade_0829_20aoi_b1 | M1 | [OPTIMAL](lade_0829_20aoi_b1_M1_exact_seed42/report.md) | 99.617699 | 99.617699 | 0.00% | — |
| lade_0829_20aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0829_20aoi_b1_M2_exact_seed42/report.md) | 92.104700 | 0.000000 | 100.00% | 5/3486784401 |
| lade_0829_20aoi_b1 | M3 | [FEASIBLE_NOT_PROVEN](lade_0829_20aoi_b1_M3_exact_seed42/report.md) | 120.368100 | 118.194022 | 1.81% | — |
| lade_0829_20aoi_b1 | M4 | [NO_SOLUTION_WITHIN_BUDGET](lade_0829_20aoi_b1_M4_exact_seed42/report.md) | — | 0.000000 | — | 0/3486784401 |
| lade_0823_30aoi_b1 | M1 | [FEASIBLE_NOT_PROVEN](lade_0823_30aoi_b1_M1_exact_seed42/report.md) | 103.387600 | 102.177724 | 1.17% | — |
| lade_0823_30aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0823_30aoi_b1_M2_exact_seed42/report.md) | 103.387600 | 0.000000 | 100.00% | 0/205891132094649 |
| lade_0823_30aoi_b1 | M3 | [FEASIBLE_NOT_PROVEN](lade_0823_30aoi_b1_M3_exact_seed42/report.md) | 153.154700 | 122.487467 | 20.02% | — |
| lade_0823_30aoi_b1 | M4 | [NO_SOLUTION_WITHIN_BUDGET](lade_0823_30aoi_b1_M4_exact_seed42/report.md) | — | 0.000000 | — | 0/205891132094649 |
| lade_0824_30aoi_b1 | M1 | [FEASIBLE_NOT_PROVEN](lade_0824_30aoi_b1_M1_exact_seed42/report.md) | 103.387600 | 101.896856 | 1.44% | — |
| lade_0824_30aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0824_30aoi_b1_M2_exact_seed42/report.md) | 106.859900 | 0.000000 | 100.00% | 0/205891132094649 |
| lade_0824_30aoi_b1 | M3 | [NO_SOLUTION_WITHIN_BUDGET](lade_0824_30aoi_b1_M3_exact_seed42/report.md) | — | 120.165860 | — | — |
| lade_0824_30aoi_b1 | M4 | [NO_SOLUTION_WITHIN_BUDGET](lade_0824_30aoi_b1_M4_exact_seed42/report.md) | — | 0.000000 | — | 0/205891132094649 |
| lade_0825_30aoi_b1 | M1 | [FEASIBLE_NOT_PROVEN](lade_0825_30aoi_b1_M1_exact_seed42/report.md) | 103.387600 | 101.823718 | 1.51% | — |
| lade_0825_30aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0825_30aoi_b1_M2_exact_seed42/report.md) | 107.964300 | 0.000000 | 100.00% | 0/205891132094649 |
| lade_0825_30aoi_b1 | M3 | [NO_SOLUTION_WITHIN_BUDGET](lade_0825_30aoi_b1_M3_exact_seed42/report.md) | — | 123.171791 | — | — |
| lade_0825_30aoi_b1 | M4 | [NO_SOLUTION_WITHIN_BUDGET](lade_0825_30aoi_b1_M4_exact_seed42/report.md) | — | 0.000000 | — | 0/205891132094649 |
| lade_0826_30aoi_b1 | M1 | [FEASIBLE_NOT_PROVEN](lade_0826_30aoi_b1_M1_exact_seed42/report.md) | 103.387600 | 101.966023 | 1.37% | — |
| lade_0826_30aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0826_30aoi_b1_M2_exact_seed42/report.md) | 144.048600 | 0.000000 | 100.00% | 0/205891132094649 |
| lade_0826_30aoi_b1 | M3 | [NO_SOLUTION_WITHIN_BUDGET](lade_0826_30aoi_b1_M3_exact_seed42/report.md) | — | 123.180064 | — | — |
| lade_0826_30aoi_b1 | M4 | [NO_SOLUTION_WITHIN_BUDGET](lade_0826_30aoi_b1_M4_exact_seed42/report.md) | — | 0.000000 | — | 0/205891132094649 |
| lade_0827_30aoi_b1 | M1 | [FEASIBLE_NOT_PROVEN](lade_0827_30aoi_b1_M1_exact_seed42/report.md) | 103.387600 | 101.519989 | 1.81% | — |
| lade_0827_30aoi_b1 | M2 | [FEASIBLE_NOT_PROVEN](lade_0827_30aoi_b1_M2_exact_seed42/report.md) | 124.632800 | 0.000000 | 100.00% | 0/205891132094649 |
| lade_0827_30aoi_b1 | M3 | [NO_SOLUTION_WITHIN_BUDGET](lade_0827_30aoi_b1_M3_exact_seed42/report.md) | — | 119.840350 | — | — |

暂停记录：2026-09-29T23:03:04.462908+08:00。主任务及两个求解进程已暂停，已完成结果保留；等待用户恢复指令。
