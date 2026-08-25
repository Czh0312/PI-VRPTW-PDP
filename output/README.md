# output 目录

`output/` 是项目唯一的通用输出目录。不要再创建同级的 `outputs/`。

- `experiments/`：启发式实验日志、汇总表和人工分析制品。
- `dataset_artifacts/`：数据集工作簿、预览图及其构建/校验脚本。

当前实验总结：

- `experiments/four_model_small_exact_experiment_summary.md`：M1—M4在22个小规模
  算例上的完整精确求解结果、耗时对比与实验结论。

精确求解批处理产生的机器可读 JSON/CSV 继续保存在 `results/`，以便与通用展示
制品分开。历史输出不会因为模型重新编号而改写；引用历史结果时仍需检查其编号口径。
