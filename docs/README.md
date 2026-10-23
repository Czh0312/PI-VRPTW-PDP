# 计划与当前说明

这里保留需要继续使用的计划、模型设计、数据口径和开发说明。同一任务更新原文，
完成后将结论并入对应说明；正式报告放入 `output/`，过期开发记录移出本目录，重复草稿及时删除。
聊天总结由现有 skill 写入 [project/](../project/README.md)。

| 用途 | 入口 |
| --- | --- |
| 开发与日常维护 | [开发指南](development.md)、[项目总览](project_overview.md) |
| 研究计划 | [论文补充实验计划](thesis_experiment_completion_plan_20261012.md)、[实验顺序](experiment_roadmap.md)、[静态对照计划](static_single_multibatch_study_plan.md)、[多批次设计方案](multibatch_container_cycle_plan.md) |
| 模型与求解范围 | [求解目录指南](solver_directory_guide.md)、[编号与代码位置](model_layout.md)、[精确模型结构](exact_model_layout.md)、[统一批次数 MILP](unified_batch_count_milp.md)、[ALNS 模型结构](alns_model_layout.md)、[精确求解范围](exact_solver_architecture.md)、[M5](model5_multibatch_cycle.md)、[M6](model6_joint_batch_cost_design.md)、[M7/M8](model7_model8_calendar_design.md) |
| 实验运行方式 | [单批次对照](single_batch_comparison.md)、[多批次运行](static_multibatch_implementation.md)、[M5/M6 成本比较](model5_model6_cost_comparison.md) |
| 数据设计 | [小规模统一7顾客的27例新版](../data/formal_benchmark27_spacetime_v4_1/README.md)、[旧空间时段版本](../data/formal_benchmark27_spacetime_v2/README.md)、[旧一客一箱版本](benchmark27_one_box_20261019.md)、[数据来源](data_status.md)、[设计审查](dataset_design_review_20260930.md)、[V2 规则](dataset_redesign_v2_20260930.md)、[M5/M6 共享数据](model5_model6_shared_dataset_20261005.md)、[M6 均匀分批数据](model6_uniform_dataset_v2_20261005.md) |
| 成本与参数口径 | [成本标定依据](cost_calibration_review_20261006.md)、[成本与批次数域](cost_and_batch_domain_20261007.md)、[运输成本比例设置](transport_container_ratio_tests_v1.md) |
| 已完成的正式结果 | [output 成果入口](../output/README.md) |
| 历史开发记录 | [archive/docs](../archive/docs/README.md) |

已实施方案可以作为模型设计依据保留，其中带日期的状态按当时版本理解。
每次修改相关模块时检查对应计划；不因一次测试或一次小修复另建记录文件。
