# 多批次：M5 / large

同一 family_id 的 B1/B2/B3 使用相同网络、需求、窗口、车队和默认池。
case.json 是运行入口；orders.json 和 policy.json 是该批次配置的订单与日程。
共用网络、车队和来源资料位于 data/shared/，不按模型复制数据。

| 案例 | 来源 | 顾客 | 订单 | 批次数 |
| --- | --- | ---: | ---: | ---: |
| [zhou_Set2_100-5-1b_subset50_b2](zhou_Set2_100-5-1b_subset50/b2/case.json) | zhou_derived_scenario | 50 | 50 | 2 |
| [zhou_Set2_100-5-1b_subset50_b3](zhou_Set2_100-5-1b_subset50/b3/case.json) | zhou_derived_scenario | 50 | 50 | 3 |
| [zhou_Set2_100-5-2_subset50_b2](zhou_Set2_100-5-2_subset50/b2/case.json) | zhou_derived_scenario | 50 | 50 | 2 |
| [zhou_Set2_100-5-2_subset50_b3](zhou_Set2_100-5-2_subset50/b3/case.json) | zhou_derived_scenario | 50 | 50 | 3 |
| [zhou_Set2_100-5-3_subset50_b2](zhou_Set2_100-5-3_subset50/b2/case.json) | zhou_derived_scenario | 50 | 50 | 2 |
| [zhou_Set2_100-5-3_subset50_b3](zhou_Set2_100-5-3_subset50/b3/case.json) | zhou_derived_scenario | 50 | 50 | 3 |
| [zhou_Set2_100-10-1_subset50_b2](zhou_Set2_100-10-1_subset50/b2/case.json) | zhou_derived_scenario | 50 | 50 | 2 |
| [zhou_Set2_100-10-1_subset50_b3](zhou_Set2_100-10-1_subset50/b3/case.json) | zhou_derived_scenario | 50 | 50 | 3 |
| [zhou_Set2_100-10-2_subset50_b2](zhou_Set2_100-10-2_subset50/b2/case.json) | zhou_derived_scenario | 50 | 50 | 2 |
| [zhou_Set2_100-10-2_subset50_b3](zhou_Set2_100-10-2_subset50/b3/case.json) | zhou_derived_scenario | 50 | 50 | 3 |
| [zhou_Set2_100-5-1b_b2](zhou_Set2_100-5-1b/b2/case.json) | zhou_derived_scenario | 100 | 100 | 2 |
| [zhou_Set2_100-5-1b_b3](zhou_Set2_100-5-1b/b3/case.json) | zhou_derived_scenario | 100 | 100 | 3 |
| [zhou_Set2_100-5-2_b2](zhou_Set2_100-5-2/b2/case.json) | zhou_derived_scenario | 100 | 100 | 2 |
| [zhou_Set2_100-5-2_b3](zhou_Set2_100-5-2/b3/case.json) | zhou_derived_scenario | 100 | 100 | 3 |
| [zhou_Set2_100-5-3_b2](zhou_Set2_100-5-3/b2/case.json) | zhou_derived_scenario | 100 | 100 | 2 |
| [zhou_Set2_100-5-3_b3](zhou_Set2_100-5-3/b3/case.json) | zhou_derived_scenario | 100 | 100 | 3 |
| [zhou_Set2_100-10-1_b2](zhou_Set2_100-10-1/b2/case.json) | zhou_derived_scenario | 100 | 100 | 2 |
| [zhou_Set2_100-10-1_b3](zhou_Set2_100-10-1/b3/case.json) | zhou_derived_scenario | 100 | 100 | 3 |
| [zhou_Set2_100-10-2_b2](zhou_Set2_100-10-2/b2/case.json) | zhou_derived_scenario | 100 | 100 | 2 |
| [zhou_Set2_100-10-2_b3](zhou_Set2_100-10-2/b3/case.json) | zhou_derived_scenario | 100 | 100 | 3 |
| [zhou_Set2_100-5-1b_subset50_scenario02_b2](zhou_Set2_100-5-1b_subset50_scenario02/b2/case.json) | zhou_derived_scenario | 50 | 50 | 2 |
| [zhou_Set2_100-5-1b_subset50_scenario02_b3](zhou_Set2_100-5-1b_subset50_scenario02/b3/case.json) | zhou_derived_scenario | 50 | 50 | 3 |
| [zhou_Set2_100-5-2_subset50_scenario02_b2](zhou_Set2_100-5-2_subset50_scenario02/b2/case.json) | zhou_derived_scenario | 50 | 50 | 2 |
| [zhou_Set2_100-5-2_subset50_scenario02_b3](zhou_Set2_100-5-2_subset50_scenario02/b3/case.json) | zhou_derived_scenario | 50 | 50 | 3 |
| [zhou_Set2_100-5-3_subset50_scenario02_b2](zhou_Set2_100-5-3_subset50_scenario02/b2/case.json) | zhou_derived_scenario | 50 | 50 | 2 |
| [zhou_Set2_100-5-3_subset50_scenario02_b3](zhou_Set2_100-5-3_subset50_scenario02/b3/case.json) | zhou_derived_scenario | 50 | 50 | 3 |
| [zhou_Set2_100-10-1_subset50_scenario02_b2](zhou_Set2_100-10-1_subset50_scenario02/b2/case.json) | zhou_derived_scenario | 50 | 50 | 2 |
| [zhou_Set2_100-10-1_subset50_scenario02_b3](zhou_Set2_100-10-1_subset50_scenario02/b3/case.json) | zhou_derived_scenario | 50 | 50 | 3 |
| [zhou_Set2_100-10-2_subset50_scenario02_b2](zhou_Set2_100-10-2_subset50_scenario02/b2/case.json) | zhou_derived_scenario | 50 | 50 | 2 |
| [zhou_Set2_100-10-2_subset50_scenario02_b3](zhou_Set2_100-10-2_subset50_scenario02/b3/case.json) | zhou_derived_scenario | 50 | 50 | 3 |
| [zhou_Set2_100-5-1b_scenario02_b2](zhou_Set2_100-5-1b_scenario02/b2/case.json) | zhou_derived_scenario | 100 | 100 | 2 |
| [zhou_Set2_100-5-1b_scenario02_b3](zhou_Set2_100-5-1b_scenario02/b3/case.json) | zhou_derived_scenario | 100 | 100 | 3 |
| [zhou_Set2_100-5-2_scenario02_b2](zhou_Set2_100-5-2_scenario02/b2/case.json) | zhou_derived_scenario | 100 | 100 | 2 |
| [zhou_Set2_100-5-2_scenario02_b3](zhou_Set2_100-5-2_scenario02/b3/case.json) | zhou_derived_scenario | 100 | 100 | 3 |
| [zhou_Set2_100-5-3_scenario02_b2](zhou_Set2_100-5-3_scenario02/b2/case.json) | zhou_derived_scenario | 100 | 100 | 2 |
| [zhou_Set2_100-5-3_scenario02_b3](zhou_Set2_100-5-3_scenario02/b3/case.json) | zhou_derived_scenario | 100 | 100 | 3 |
| [zhou_Set2_100-10-1_scenario02_b2](zhou_Set2_100-10-1_scenario02/b2/case.json) | zhou_derived_scenario | 100 | 100 | 2 |
| [zhou_Set2_100-10-1_scenario02_b3](zhou_Set2_100-10-1_scenario02/b3/case.json) | zhou_derived_scenario | 100 | 100 | 3 |
| [zhou_Set2_100-10-2_scenario02_b2](zhou_Set2_100-10-2_scenario02/b2/case.json) | zhou_derived_scenario | 100 | 100 | 2 |
| [zhou_Set2_100-10-2_scenario02_b3](zhou_Set2_100-10-2_scenario02/b3/case.json) | zhou_derived_scenario | 100 | 100 | 3 |
