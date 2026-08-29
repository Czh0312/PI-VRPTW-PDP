# M1/M2/M3/M4/M6 ALNS规模实验

论文引用时优先使用两组 `*_final.jsonl` / `*_final.csv`：

- `small_standard_same_day_alns_seed42_final.*`：22个小规模算例×5个模型，共110行。
- `large_standard_same_day_alns_seed42_final.*`：17个大规模算例×5个模型，共85行。

大规模 `*_final.*` 已更新为2026-08-29升级后五模型完整重跑批次
`large_standard_same_day_t10_seed42_20260829_171003.*`。带时间戳的文件
保留所有历史批次；小规模ALNS仍沿用2026-08-26的验证结果，小规模精确
基准已更新到 `output/results/mainline_small_exact/small_exact_final.*`。

实验说明见 `output/experiments/model12346_alns_scaling_experiment.md`。
