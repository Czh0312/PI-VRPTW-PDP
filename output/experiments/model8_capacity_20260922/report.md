# M8 容量协作与回收重分配实验

## Material Passport

- 状态：实际执行；构造例与原始 Zhou 数据分别报告。
- 目标：同样任务、同仓、既有车辆下最小化两级总距离。
- 固定 E1 路线及配送车辆/顺序，释放回收归属/顺序；异步交接同质空箱，每箱最多一次。
- 基础解未证明最优时明确标记；第二阶段精确证书只针对给定配送骨架。
- control 与 relay 的回收调整权限相同，仅 relay 允许空箱交接。
- ALNS 同预算差值为算法比较，不是因果或全局最优传递价值；总改善不全部归因于传递。
- 随机种子：[42, 7, 19]；每个第二阶段预算 20.0s；硬超时 90s。
- 证据：各实例下 JSON 保存基线指纹、完整路线、数量、时刻、逐段载荷、搜索历史及独立校验。
- 历史 M8 结果位于 `output/experiments/model8_large_probe_20260921`，旧实现保留在 `model_8/legacy`。

| 实例 | 方法/种子 | 基础距离 | M3证明 | 最终距离 | 节省% | 交接次数 | 迭代数 | 条件最优 | 耗时s |
|---|---|---:|---|---:|---:|---:|---:|---|---:|
| mechanism_tight | alns_control_19 | 130.929222 | True | 130.929222 | 0.0000 | 0 | 136924 | False | 20.00 |
| mechanism_tight | alns_control_42 | 130.929222 | True | 130.929222 | 0.0000 | 0 | 137743 | False | 20.00 |
| mechanism_tight | alns_control_7 | 130.929222 | True | 130.929222 | 0.0000 | 0 | 132891 | False | 20.00 |
| mechanism_tight | alns_relay_19 | 130.929222 | True | 116.787087 | 10.8014 | 1 | 121942 | False | 20.00 |
| mechanism_tight | alns_relay_42 | 130.929222 | True | 116.787087 | 10.8014 | 1 | 118060 | False | 20.00 |
| mechanism_tight | alns_relay_7 | 130.929222 | True | 116.787087 | 10.8014 | 1 | 116460 | False | 20.00 |
| mechanism_tight | exact_control_42 | 130.929222 | True | 130.929222 | 0.0000 | 0 | None | True | 0.02 |
| mechanism_tight | exact_relay_42 | 130.929222 | True | 116.787087 | 10.8014 | 1 | None | True | 0.19 |
| mechanism_loose | alns_control_19 | 116.787087 | True | 116.787087 | 0.0000 | 0 | 118299 | False | 20.00 |
| mechanism_loose | alns_control_42 | 116.787087 | True | 116.787087 | 0.0000 | 0 | 114803 | False | 20.00 |
| mechanism_loose | alns_control_7 | 116.787087 | True | 116.787087 | 0.0000 | 0 | 121478 | False | 20.00 |
| mechanism_loose | alns_relay_19 | 116.787087 | True | 116.787087 | 0.0000 | 0 | 111983 | False | 20.00 |
| mechanism_loose | alns_relay_42 | 116.787087 | True | 116.787087 | 0.0000 | 0 | 110734 | False | 20.00 |
| mechanism_loose | alns_relay_7 | 116.787087 | True | 116.787087 | 0.0000 | 0 | 110013 | False | 20.00 |
| mechanism_loose | exact_control_42 | 116.787087 | True | 116.787087 | 0.0000 | 0 | None | True | 0.02 |
| mechanism_loose | exact_relay_42 | 116.787087 | True | 116.787087 | 0.0000 | 0 | None | True | 0.10 |
| Set2_100-10-1 | alns_control_19 | 1449.239074 | True | 1449.239074 | 0.0000 | 0 | 8122 | False | 20.00 |
| Set2_100-10-1 | alns_control_42 | 1449.239074 | True | 1449.239074 | 0.0000 | 0 | 8200 | False | 20.00 |
| Set2_100-10-1 | alns_control_7 | 1449.239074 | True | 1449.239074 | 0.0000 | 0 | 7847 | False | 20.00 |
| Set2_100-10-1 | alns_relay_19 | 1449.239074 | True | 1449.239074 | 0.0000 | 0 | 5814 | False | 20.00 |
| Set2_100-10-1 | alns_relay_42 | 1449.239074 | True | 1449.239074 | 0.0000 | 0 | 5977 | False | 20.00 |
| Set2_100-10-1 | alns_relay_7 | 1449.239074 | True | 1449.239074 | 0.0000 | 0 | 5700 | False | 20.00 |
| Set2_200-10-1 | alns_control_19 | 2157.073012 | False | 2156.422731 | 0.0301 | 0 | 5900 | False | 20.00 |
| Set2_200-10-1 | alns_control_42 | 2157.073012 | False | 2157.073012 | 0.0000 | 0 | 5859 | False | 20.00 |
| Set2_200-10-1 | alns_control_7 | 2157.073012 | False | 2157.073012 | 0.0000 | 0 | 5940 | False | 20.00 |
| Set2_200-10-1 | alns_relay_19 | 2157.073012 | False | 2157.073012 | 0.0000 | 0 | 2665 | False | 20.00 |
| Set2_200-10-1 | alns_relay_42 | 2157.073012 | False | 2157.073012 | 0.0000 | 0 | 2927 | False | 20.00 |
| Set2_200-10-1 | alns_relay_7 | 2157.073012 | False | 2157.073012 | 0.0000 | 0 | 2573 | False | 20.00 |

## 完成与超时

- mechanism_tight / base / 42: completed (0.13s)
- mechanism_tight / exact_control / 42: completed (0.08s)
- mechanism_tight / exact_relay / 42: completed (0.29s)
- mechanism_tight / alns_control / 42: completed (20.08s)
- mechanism_tight / alns_relay / 42: completed (20.08s)
- mechanism_tight / alns_control / 7: completed (20.06s)
- mechanism_tight / alns_relay / 7: completed (20.09s)
- mechanism_tight / alns_control / 19: completed (20.09s)
- mechanism_tight / alns_relay / 19: completed (20.09s)
- mechanism_loose / base / 42: completed (0.13s)
- mechanism_loose / exact_control / 42: completed (0.13s)
- mechanism_loose / exact_relay / 42: completed (0.24s)
- mechanism_loose / alns_control / 42: completed (20.10s)
- mechanism_loose / alns_relay / 42: completed (20.07s)
- mechanism_loose / alns_control / 7: completed (20.11s)
- mechanism_loose / alns_relay / 7: completed (20.05s)
- mechanism_loose / alns_control / 19: completed (20.05s)
- mechanism_loose / alns_relay / 19: completed (20.10s)
- Set2_100-10-1 / base / 42: completed (0.08s)
- Set2_100-10-1 / alns_control / 42: completed (20.09s)
- Set2_100-10-1 / alns_relay / 42: completed (20.10s)
- Set2_100-10-1 / alns_control / 7: completed (20.09s)
- Set2_100-10-1 / alns_relay / 7: completed (20.07s)
- Set2_100-10-1 / alns_control / 19: completed (20.07s)
- Set2_100-10-1 / alns_relay / 19: completed (20.10s)
- Set2_200-10-1 / base / 42: completed (0.08s)
- Set2_200-10-1 / alns_control / 42: completed (20.07s)
- Set2_200-10-1 / alns_relay / 42: completed (20.10s)
- Set2_200-10-1 / alns_control / 7: completed (20.09s)
- Set2_200-10-1 / alns_relay / 7: completed (20.10s)
- Set2_200-10-1 / alns_control / 19: completed (20.11s)
- Set2_200-10-1 / alns_relay / 19: completed (20.07s)
