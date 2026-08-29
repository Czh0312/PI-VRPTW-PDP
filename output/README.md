# output 目录

`output/` 是项目唯一的通用输出目录。不要再创建同级的 `outputs/`。

- `experiments/`：启发式实验日志、汇总表和人工分析制品。
- `results/`：精确求解和批处理产生的机器可读 JSON、JSONL、CSV。
- `dataset_artifacts/`：数据集工作簿、预览图及其构建/校验脚本。
- `economic_analysis*/`：成本分析表、图片和 Markdown 报告。
- `m6_transshipment_validation*/`：真实场景下的 Model 6 中转验证结果。

当前实验总结：

- `experiments/four_model_small_exact_experiment_summary.md`：M1、M2、M3、M4、M6、M7
  在22个标准小规模算例上的精确求解结果、证明状态、耗时对比与实验结论；
  六模型合并数据位于`results/mainline_small_exact/small_exact_models123467_final.*`。
- `experiments/model12346_alns_scaling_experiment.md`：M1、M2、M3、M4、M6、M7
  的ALNS规模对比，包含大规模17个公共实例的六模型逐例结果、配对改善和中转收益；
  六模型合并数据位于`results/model12346_alns_scaling/large_standard_same_day_alns_seed42_all_models_final.*`。
- `experiments/alns_equal_time_vs_nested_experiment.md`：独立等时间与嵌套增量
  两种ALNS协议的对照，专门检验扩展模型的支配性和中转机制收益。
- `experiments/model123467_incumbent_preserving_full_experiment.md`：17个大规模
  公共实例上的完整 incumbent 保留实验；68/68组全部可行且保持扩展模型不劣，
  正式机器可读结果位于`results/model123467_nested_full_final/`。
- `experiments/small_alns_time_to_exact_experiment.md`：小规模下ALNS首次
  命中已证明精确最优值的时间、命中率和配对加速比。
- `experiments/small_alns_tuned_exact_comparison.md`：补齐关键邻域后的最终
  110组小规模实验；总体平均gap为0.1037%，97/110组命中精确最优。
- `experiments/model7_exact_and_large_alns_experiment.md`：Model 7 在22个
  Set1小规模实例上的120秒精确实验，以及18个Set2大规模实例上的10秒
  ALNS实验；对应稳定CSV/JSONL位于`results/model7_exact_and_alns/`。

当前只保留可追溯到 `data/zhou_later_pickup/` 或 `data/real_case/` 的结果。
旧数据集、已移除数据集和无持久化数据源的随机合成算例结果不放在本目录。
