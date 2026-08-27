# output 目录

`output/` 是项目唯一的通用输出目录。不要再创建同级的 `outputs/`。

- `experiments/`：启发式实验日志、汇总表和人工分析制品。
- `dataset_artifacts/`：数据集工作簿、预览图及其构建/校验脚本。

当前实验总结：

- `experiments/four_model_small_exact_experiment_summary.md`：M1、M2、M3、M4及M6
  在22个标准小规模算例上的完整精确求解结果、耗时对比与实验结论。
- `experiments/model12346_alns_scaling_experiment.md`：M1、M2、M3、M4、M6
  的ALNS小/大规模对比，包含小规模精确gap、大规模配对改善和M6中转收益。
- `experiments/alns_equal_time_vs_nested_experiment.md`：独立等时间与嵌套增量
  两种ALNS协议的对照，专门检验扩展模型的支配性和中转机制收益。
- `experiments/small_alns_time_to_exact_experiment.md`：小规模下ALNS首次
  命中已证明精确最优值的时间、命中率和配对加速比。
- `experiments/small_alns_tuned_exact_comparison.md`：补齐关键邻域后的最终
  110组小规模实验；总体平均gap为0.1037%，97/110组命中精确最优。

精确求解批处理产生的机器可读 JSON/CSV 继续保存在 `results/`，以便与通用展示
制品分开。历史输出不会因为模型重新编号而改写；引用历史结果时仍需检查其编号口径。
