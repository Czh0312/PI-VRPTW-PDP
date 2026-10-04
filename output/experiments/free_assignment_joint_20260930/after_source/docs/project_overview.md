# 项目总览

当前规则为 `multi_batch_cutoff_v1`。B=1 支持 M1–M4 的一次性/循环包装与固定/自由分仓对照；B>1 由 M5 支持跨批周转。
新版 M1/M3 使用共同事件求解器，M2/M4 的精确法按规模采用联合顾客分仓 MILP 或完整枚举，ALNS 使用启发式分仓搜索。

循环方案保持固定订单批次、一级先送后取、截止库存全取、有限箱池、清洗后释放和全池清洁末态。
一次性方案只配送，每周期重新购买包装。四模型使用相同输入和配送标准，并审计物理车辆与两周期重放。

旧 M5 中转、旧 M6–M8、旧经济报告和旧实验已归档；当前 M5 专指多批次模型。历史 M1–M4 算法仅在显式 --baseline 入口保留，不参与新版对照。
单批生命周期核算与敏感性已接入，当前参数为虚构演示；真实回收、清洗、损耗、寿命和费用数据仍缺。

- [单批四模型、经济条件与验证](single_batch_comparison.md)
- [模型、命令和目录](../README.md)
- [当前研究计划](static_single_multibatch_study_plan.md)
- [多批使用说明](model5_multibatch_cycle.md)
- [数据状态](data_status.md)
- [清理记录](repository_cleanup_20260929.md)

各模型独立位于 `models/routing/variants/model_1/`～`model_5/`，共享业务规则在 `models/routing/common/cycle/`，精确求解与 ALNS 分别位于 `models/routing/solvers/exact/` 和 `models/routing/solvers/alns/`。见 [模型布局](model_layout.md)。
