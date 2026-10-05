# 模型五与模型六成本对比

M5以距离为目标，求解结束并通过独立审计后，按与M6相同的费用配置计算成本。M5代表方案按最短距离选取，同距离时按成本排序；M6按已观察到的最低成本选取。

主对比使用M5实际配置的箱池。另列同一路线与时间表的最低需箱量计价，作为箱池配置的参考，不改写M5求解结果，也不代表另求得的成本最优方案。

| 算例 | 单位 | M5成本 | M6成本 | 节省额 | 节省比例% | M5同时间表最低箱池成本 | 相对此参考节省% |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| lade_0823_10aoi__spread_medium | synthetic_units | 1175.3557 | 931.8179 | 243.5377 | 20.7203 | 1005.3557 | 7.3146 |
| lade_0823_20aoi__spread_medium | synthetic_units | 1782.6686 | 1503.2361 | 279.4325 | 15.6750 | 1677.6686 | 10.3973 |
| lade_0823_30aoi__spread_medium | synthetic_units | 2174.0022 | 1795.1363 | 378.8659 | 17.4271 | 1984.0022 | 9.5194 |
| zhou_Set1_10-2-1__spread_medium | synthetic_units | 866.8736 | 523.4656 | 343.4081 | 39.6145 | 856.8736 | 38.9098 |
| zhou_Set1_12-2-1__spread_medium | synthetic_units | 1026.3953 | 789.3968 | 236.9985 | 23.0904 | 1011.3953 | 21.9497 |
| zhou_Set1_7-2-1__spread_medium | synthetic_units | 917.9405 | 588.9868 | 328.9536 | 35.8361 | 892.9405 | 34.0396 |
| zhou_Set2_100-10-1__spread_medium | synthetic_units | 5312.3768 | 4893.9318 | 418.4449 | 7.8768 | 5247.3768 | 6.7357 |
| zhou_Set2_100-10-1_subset20__spread_medium | synthetic_units | 1584.4656 | 1233.7664 | 350.6993 | 22.1336 | 1559.4656 | 20.8853 |
| zhou_Set2_100-10-1_subset30__spread_medium | synthetic_units | 1987.4544 | 1778.7864 | 208.6680 | 10.4993 | 1962.4544 | 9.3591 |
| zhou_Set2_100-10-1_subset50__spread_medium | synthetic_units | 3018.7801 | 2775.0879 | 243.6922 | 8.0725 | 2983.7801 | 6.9942 |

节省比例=(M5成本−M6成本)/M5成本×100%；负数表示M6成本更高，M5成本为零时比例留空。购置投入单独报告，不叠加到周期成本。

## 每种求解方法的全部运行

| 算例 | 方法 | 模型 | 有效/尝试 | 最低成本 | 平均成本 | 最高成本 | 样本标准差 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| lade_0823_10aoi__spread_medium | alns | M5_distance | 1/1 | 1175.3557 | 1175.3557 | 1175.3557 | — |
| lade_0823_10aoi__spread_medium | alns | M6_uniform | 1/1 | 931.8179 | 931.8179 | 931.8179 | — |
| lade_0823_20aoi__spread_medium | alns | M5_distance | 1/1 | 1782.6686 | 1782.6686 | 1782.6686 | — |
| lade_0823_20aoi__spread_medium | alns | M6_uniform | 1/1 | 1503.2361 | 1503.2361 | 1503.2361 | — |
| lade_0823_30aoi__spread_medium | alns | M5_distance | 1/1 | 2174.0022 | 2174.0022 | 2174.0022 | — |
| lade_0823_30aoi__spread_medium | alns | M6_uniform | 1/1 | 1795.1363 | 1795.1363 | 1795.1363 | — |
| zhou_Set1_10-2-1__spread_medium | alns | M5_distance | 1/1 | 866.8736 | 866.8736 | 866.8736 | — |
| zhou_Set1_10-2-1__spread_medium | alns | M6_uniform | 1/1 | 523.4656 | 523.4656 | 523.4656 | — |
| zhou_Set1_12-2-1__spread_medium | alns | M5_distance | 1/1 | 1026.3953 | 1026.3953 | 1026.3953 | — |
| zhou_Set1_12-2-1__spread_medium | alns | M6_uniform | 1/1 | 789.3968 | 789.3968 | 789.3968 | — |
| zhou_Set1_7-2-1__spread_medium | alns | M5_distance | 1/1 | 917.9405 | 917.9405 | 917.9405 | — |
| zhou_Set1_7-2-1__spread_medium | alns | M6_uniform | 1/1 | 588.9868 | 588.9868 | 588.9868 | — |
| zhou_Set2_100-10-1__spread_medium | alns | M5_distance | 1/1 | 5312.3768 | 5312.3768 | 5312.3768 | — |
| zhou_Set2_100-10-1__spread_medium | alns | M6_uniform | 1/1 | 4893.9318 | 4893.9318 | 4893.9318 | — |
| zhou_Set2_100-10-1_subset20__spread_medium | alns | M5_distance | 1/1 | 1584.4656 | 1584.4656 | 1584.4656 | — |
| zhou_Set2_100-10-1_subset20__spread_medium | alns | M6_uniform | 1/1 | 1233.7664 | 1233.7664 | 1233.7664 | — |
| zhou_Set2_100-10-1_subset30__spread_medium | alns | M5_distance | 1/1 | 1987.4544 | 1987.4544 | 1987.4544 | — |
| zhou_Set2_100-10-1_subset30__spread_medium | alns | M6_uniform | 1/1 | 1778.7864 | 1778.7864 | 1778.7864 | — |
| zhou_Set2_100-10-1_subset50__spread_medium | alns | M5_distance | 1/1 | 3018.7801 | 3018.7801 | 3018.7801 | — |
| zhou_Set2_100-10-1_subset50__spread_medium | alns | M6_uniform | 1/1 | 2775.0879 | 2775.0879 | 2775.0879 | — |

同方法同种子的逐对成本及失败状态见cost_pairs.csv，逐项费用见cost_components.csv，代表方案、证明目标和购置投入见cost_summary.json。原始结果、重放审计和comparison_cost.json保存在每次运行目录。

两模型的费用配置哈希与评价期必须一致。均匀日历与目标函数同时变化，结果用于整体方案对比。合成单位不换算为真实人民币收益；限时方案不称为成本最优。
