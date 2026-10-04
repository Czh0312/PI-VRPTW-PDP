# 单批次：M1–M4 对比 / small

同一 family_id 的 B1/B2/B3 使用相同网络、需求、窗口、车队和默认池。
case.json 是运行入口；orders.json 和 policy.json 是该批次配置的订单与日程。
共用网络、车队和来源资料位于 data/shared/，不按模型复制数据。

| 案例 | 来源 | 顾客 | 订单 | 批次数 |
| --- | --- | ---: | ---: | ---: |
| [mechanism_forced_late_b1](mechanism_forced_late/case.json) | synthetic_mechanism | 2 | 6 | 1 |
| [mechanism_cutoff_choice_b1](mechanism_cutoff_choice/case.json) | synthetic_mechanism | 2 | 6 | 1 |
| [zhou_Set1_7-2-1_b1](zhou_Set1_7-2-1/case.json) | zhou_derived_scenario | 7 | 7 | 1 |
| [zhou_Set1_7-2-1b_b1](zhou_Set1_7-2-1b/case.json) | zhou_derived_scenario | 7 | 7 | 1 |
| [zhou_Set1_7-2-2_b1](zhou_Set1_7-2-2/case.json) | zhou_derived_scenario | 7 | 7 | 1 |
| [zhou_Set1_7-2-2b_b1](zhou_Set1_7-2-2b/case.json) | zhou_derived_scenario | 7 | 7 | 1 |
| [zhou_Set1_7-2-3_b1](zhou_Set1_7-2-3/case.json) | zhou_derived_scenario | 7 | 7 | 1 |
| [zhou_Set1_10-2-1_b1](zhou_Set1_10-2-1/case.json) | zhou_derived_scenario | 10 | 10 | 1 |
| [zhou_Set1_10-2-1b_b1](zhou_Set1_10-2-1b/case.json) | zhou_derived_scenario | 10 | 10 | 1 |
| [zhou_Set1_10-2-2_b1](zhou_Set1_10-2-2/case.json) | zhou_derived_scenario | 10 | 10 | 1 |
| [zhou_Set1_10-2-2b_b1](zhou_Set1_10-2-2b/case.json) | zhou_derived_scenario | 10 | 10 | 1 |
| [zhou_Set1_10-2-3_b1](zhou_Set1_10-2-3/case.json) | zhou_derived_scenario | 10 | 10 | 1 |
| [zhou_Set1_12-2-1_b1](zhou_Set1_12-2-1/case.json) | zhou_derived_scenario | 12 | 12 | 1 |
| [zhou_Set1_12-2-1b_b1](zhou_Set1_12-2-1b/case.json) | zhou_derived_scenario | 12 | 12 | 1 |
| [zhou_Set1_12-2-2_b1](zhou_Set1_12-2-2/case.json) | zhou_derived_scenario | 12 | 12 | 1 |
| [zhou_Set1_12-2-2b_b1](zhou_Set1_12-2-2b/case.json) | zhou_derived_scenario | 12 | 12 | 1 |
| [zhou_Set1_12-2-3_b1](zhou_Set1_12-2-3/case.json) | zhou_derived_scenario | 12 | 12 | 1 |
| [zhou_Set1_7-2-1_scenario02_b1](zhou_Set1_7-2-1_scenario02/case.json) | zhou_derived_scenario | 7 | 7 | 1 |
| [zhou_Set1_7-2-1b_scenario02_b1](zhou_Set1_7-2-1b_scenario02/case.json) | zhou_derived_scenario | 7 | 7 | 1 |
| [zhou_Set1_7-2-2_scenario02_b1](zhou_Set1_7-2-2_scenario02/case.json) | zhou_derived_scenario | 7 | 7 | 1 |
| [zhou_Set1_7-2-2b_scenario02_b1](zhou_Set1_7-2-2b_scenario02/case.json) | zhou_derived_scenario | 7 | 7 | 1 |
| [zhou_Set1_7-2-3_scenario02_b1](zhou_Set1_7-2-3_scenario02/case.json) | zhou_derived_scenario | 7 | 7 | 1 |
| [zhou_Set1_10-2-1_scenario02_b1](zhou_Set1_10-2-1_scenario02/case.json) | zhou_derived_scenario | 10 | 10 | 1 |
| [zhou_Set1_10-2-1b_scenario02_b1](zhou_Set1_10-2-1b_scenario02/case.json) | zhou_derived_scenario | 10 | 10 | 1 |
| [zhou_Set1_10-2-2_scenario02_b1](zhou_Set1_10-2-2_scenario02/case.json) | zhou_derived_scenario | 10 | 10 | 1 |
| [zhou_Set1_10-2-2b_scenario02_b1](zhou_Set1_10-2-2b_scenario02/case.json) | zhou_derived_scenario | 10 | 10 | 1 |
| [zhou_Set1_10-2-3_scenario02_b1](zhou_Set1_10-2-3_scenario02/case.json) | zhou_derived_scenario | 10 | 10 | 1 |
| [zhou_Set1_12-2-1_scenario02_b1](zhou_Set1_12-2-1_scenario02/case.json) | zhou_derived_scenario | 12 | 12 | 1 |
| [zhou_Set1_12-2-1b_scenario02_b1](zhou_Set1_12-2-1b_scenario02/case.json) | zhou_derived_scenario | 12 | 12 | 1 |
| [zhou_Set1_12-2-2_scenario02_b1](zhou_Set1_12-2-2_scenario02/case.json) | zhou_derived_scenario | 12 | 12 | 1 |
| [zhou_Set1_12-2-2b_scenario02_b1](zhou_Set1_12-2-2b_scenario02/case.json) | zhou_derived_scenario | 12 | 12 | 1 |
| [zhou_Set1_12-2-3_scenario02_b1](zhou_Set1_12-2-3_scenario02/case.json) | zhou_derived_scenario | 12 | 12 | 1 |
| [lade_0823_10aoi_b1](lade_0823_10aoi/case.json) | lade_derived_scenario | 10 | 10 | 1 |
| [lade_0824_10aoi_b1](lade_0824_10aoi/case.json) | lade_derived_scenario | 10 | 10 | 1 |
| [lade_0825_10aoi_b1](lade_0825_10aoi/case.json) | lade_derived_scenario | 10 | 10 | 1 |
| [lade_0826_10aoi_b1](lade_0826_10aoi/case.json) | lade_derived_scenario | 10 | 10 | 1 |
| [lade_0827_10aoi_b1](lade_0827_10aoi/case.json) | lade_derived_scenario | 10 | 10 | 1 |
| [lade_0828_10aoi_b1](lade_0828_10aoi/case.json) | lade_derived_scenario | 10 | 10 | 1 |
| [lade_0829_10aoi_b1](lade_0829_10aoi/case.json) | lade_derived_scenario | 10 | 10 | 1 |
