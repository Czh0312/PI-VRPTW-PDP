# M1/M2/M3/M4/M6 ALNS嵌套增量实验

实验协议：M1、M3分别先运行10秒；M2继承M1的最终可行解，M4、M6继承M3的最终可行解，再运行2秒扩展搜索。随机种子为42，使用`config/fleet_config_large.json`。

- `nested_large_base10_ext2_seed42_final.jsonl`：最终51条机器可读记录，17个可行大规模算例 × 3种扩展。
- `nested_large_base10_ext2_seed42_final.csv`：同一结果的表格版本。
- `nested_large_base10_ext2_seed42_20260826_184752.*`：M2/M4原始批次；其中旧M6记录不用于最终汇总。
- `nested_large_base10_ext2_seed42_20260826_185706.*`：修正全局后处理时间预算后的M6原始批次。

`Set2_200-10-3`因固定分配下最早到达时间超过顾客C93截止时间而结构不可行，故排除；其可分配版本`Set2_200-10-3b`保留。

完整解读见`output/experiments/alns_equal_time_vs_nested_experiment.md`。
