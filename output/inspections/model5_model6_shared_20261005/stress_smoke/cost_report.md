# 模型五与模型六成本对比

M5以距离为目标，求解结束并通过独立审计后，按与M6相同的费用配置计算成本。M5代表方案按最短距离选取，同距离时按成本排序；M6按已观察到的最低成本选取。

主对比使用M5实际配置的箱池。另列同一路线与时间表的最低需箱量计价，作为箱池配置的参考，不改写M5求解结果，也不代表另求得的成本最优方案。

| 算例 | 单位 | M5成本 | M6成本 | 节省额 | 节省比例% | M5同时间表最低箱池成本 | 相对此参考节省% |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| lade_0823_10aoi__release_staggered | synthetic_units | 1310.1488 | 983.1559 | 326.9930 | 24.9585 | 1085.1488 | 9.3990 |
| lade_0823_10aoi__repeat_orders | synthetic_units | 1518.6396 | 1476.9152 | 41.7244 | 2.7475 | 1423.6396 | -3.7422 |
| mechanism_cutoff_choice__legacy_control | synthetic_units | 145.7069 | 117.9804 | 27.7265 | 19.0289 | 145.7069 | 19.0289 |
| mechanism_forced_late__legacy_control | synthetic_units | 145.7069 | 138.5000 | 7.2069 | 4.9461 | 145.7069 | 4.9461 |
| zhou_Set1_7-2-1__release_staggered | — | — | — | — | — | — | — |
| zhou_Set1_7-2-1__repeat_orders | synthetic_units | — | — | — | — | — | — |

节省比例=(M5成本−M6成本)/M5成本×100%；负数表示M6成本更高，M5成本为零时比例留空。购置投入单独报告，不叠加到周期成本。

## 每种求解方法的全部运行

| 算例 | 方法 | 模型 | 有效/尝试 | 最低成本 | 平均成本 | 最高成本 | 样本标准差 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| lade_0823_10aoi__release_staggered | alns | M5_distance | 1/1 | 1310.1488 | 1310.1488 | 1310.1488 | — |
| lade_0823_10aoi__release_staggered | alns | M6_uniform | 1/1 | 983.1559 | 983.1559 | 983.1559 | — |
| lade_0823_10aoi__repeat_orders | alns | M5_distance | 1/1 | 1518.6396 | 1518.6396 | 1518.6396 | — |
| lade_0823_10aoi__repeat_orders | alns | M6_uniform | 1/1 | 1476.9152 | 1476.9152 | 1476.9152 | — |
| mechanism_cutoff_choice__legacy_control | alns | M5_distance | 1/1 | 145.7069 | 145.7069 | 145.7069 | — |
| mechanism_cutoff_choice__legacy_control | alns | M6_uniform | 1/1 | 117.9804 | 117.9804 | 117.9804 | — |
| mechanism_forced_late__legacy_control | alns | M5_distance | 1/1 | 145.7069 | 145.7069 | 145.7069 | — |
| mechanism_forced_late__legacy_control | alns | M6_uniform | 1/1 | 138.5000 | 138.5000 | 138.5000 | — |
| zhou_Set1_7-2-1__release_staggered | alns | M5_distance | 0/1 | — | — | — | — |
| zhou_Set1_7-2-1__release_staggered | alns | M6_uniform | 0/1 | — | — | — | — |
| zhou_Set1_7-2-1__repeat_orders | alns | M5_distance | 0/1 | — | — | — | — |
| zhou_Set1_7-2-1__repeat_orders | alns | M6_uniform | 0/1 | — | — | — | — |

同方法同种子的逐对成本及失败状态见cost_pairs.csv，逐项费用见cost_components.csv，代表方案、证明目标和购置投入见cost_summary.json。原始结果、重放审计和comparison_cost.json保存在每次运行目录。

两模型的费用配置哈希与评价期必须一致。均匀日历与目标函数同时变化，结果用于整体方案对比。合成单位不换算为真实人民币收益；限时方案不称为成本最优。
