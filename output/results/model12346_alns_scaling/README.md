# M1/M2/M3/M4/M6 ALNS规模实验

论文引用时优先使用两组 `*_final.jsonl` / `*_final.csv`：

- `small_standard_same_day_alns_seed42_final.*`：22个小规模算例×5个模型，共110行。
- `large_standard_same_day_alns_seed42_final.*`：17个大规模算例×5个模型，共85行。

带时间戳的文件保留了原始批次。`20260826_182757` 和
`20260826_182515` 是修正一级车等待语义后单独重跑的M6结果；
`*_final.*` 已将它们与M1–M4基线正确合并。

实验说明见 `output/experiments/model12346_alns_scaling_experiment.md`。
