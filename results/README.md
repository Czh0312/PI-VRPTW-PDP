# 当前实验结果

本目录只接收采用当前模型编号和当前 Zhou 数据目录结构生成的新结果。

- 模型编号：M1、M2、M3、M4（以及后续 M5、M6、M7）。
- 数据定位字段：`scale_group`、`scenario`、`instance`。
- 当前小算例入口：`experiments/run_mainline_small_exact.py`。
- Model 5 全部 66 个小规模实例结果：`model5_all_small_exact/README.md`。
- Model 6 标准场景22个小规模无时限精确结果：
  `model6_standard_small_exact_unlimited/README.md`。
- M1/M2/M3/M4/M6 ALNS小/大规模结果：
  `model12346_alns_scaling/README.md`。
- ALNS嵌套增量实验（M2←M1、M4/M6←M3）：
  `model12346_nested_extensions/README.md`。
- 小规模ALNS首次命中精确最优值的time-to-target实验：
  `small_alns_time_to_exact/README.md`。
- 改进邻域后的最终小规模ALNS结果：`small_alns_tuned_final/README.md`。
- Model 5 接力严格减少路径的机制实例：`model5_relay_benefit_seed105.md`。

迁移前结果位于 `archive/`，不得直接混入当前结果汇总。
