# 改进 ALNS 的最终小规模结果

本目录保存模型 1、2、3、4、6 在 22 个 `standard_same_day` 小规模算例上的
统一 5 秒、4 起点、seed 42 实验结果，共 110 个组合。

- JSONL：`small_time_to_exact_t5_starts4_seed42_20260827_163529.jsonl`
- CSV：`small_time_to_exact_t5_starts4_seed42_20260827_163529.csv`
- 分析报告：`../../output/experiments/small_alns_tuned_exact_comparison.md`

完整性检查：110/110 组合产生可行解，无脚本错误；路线成本审计最大绝对误差为
`1.7053e-13`。
