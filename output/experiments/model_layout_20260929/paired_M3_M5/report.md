多批次循环系统验证结果

共 4 次运行，4 个解通过独立审计。
未找到解与已证明不可行分开记录；ALNS 不声明最优性。

| 运行 | 状态 | 审计 | 距离 | 已证最优 | 相对最优 gap (%) |
| --- | --- | --- | ---: | --- | ---: |
| [000_mechanism_forced_late_b1_N6_exact_seed42](000_mechanism_forced_late_b1_N6_exact_seed42/result.json) | OPTIMAL | True | 20.0 | True | 0.000000 |
| [000_mechanism_forced_late_b1_N6_alns_seed42](000_mechanism_forced_late_b1_N6_alns_seed42/result.json) | FEASIBLE | True | 20.0 | False | 0.000000 |
| [001_mechanism_forced_late_b3_N6_exact_seed42](001_mechanism_forced_late_b3_N6_exact_seed42/result.json) | OPTIMAL | True | 55.31370849898476 | True | 0.000000 |
| [001_mechanism_forced_late_b3_N6_alns_seed42](001_mechanism_forced_late_b3_N6_alns_seed42/result.json) | FEASIBLE | True | 55.31370849898476 | False | 0.000000 |

gap 仅在同一案例和同一容器池存在已证最优参考时计算。
各运行目录保存实际时间表、两个周期的重放审计、输入与源码快照。
机制示例属于情景数据；不能据此推出大规模性能或真实经营费用结论。
