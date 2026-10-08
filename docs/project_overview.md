# 项目总览

当前规则为 `multi_batch_cutoff_v1`。M1–M4 支持单批次的一次性/循环包装与固定/自由分仓对照；M5 支持固定分仓、多批次容器周转。M6–M8 联合优化批次数、箱池及路线成本，分别使用等长共同分界、等长独立分界和不等长独立分界。

循环方案包含一级先送后取、截止库存全取、有限箱池、清洗后释放和周期末全池清洁归仓。M1–M5 的日历与订单批次给定，M6–M8 在声明的允许域内优化日历及订单分批。可行结果须通过独立物理审计与周期重放。

M2/M4 的精确法按规模采用联合分仓 MILP 或分仓域树，完整枚举作为显式对照；ALNS 使用启发式分仓搜索。精确与 ALNS 按需加载、互不导入；ALNS 不加载 Gurobi。

当前模型独立位于 `models/routing/variants/model_1/`～`model_8/`。M1–M5 的共享求解内核在 `models/routing/solvers/`，M6–M8 的成本与日历实现在编号目录及 `variants/calendar_search.py`。共同业务规则在 `models/routing/common/cycle/`，成本核算在 `models/cost/`。

历史 M1–M4 仅在显式 `--baseline` 入口保留。旧 M5–M8 已归档，与当前同编号模型定义不同。研究费用包含合成情景和有来源说明的参考参数；真实容器回收、清洗、损耗及完整经营费用仍有缺口。

- [快速运行和目录职责](../README.md)
- [完整文档导航](README.md)
- [开发、验证和复现](development.md)
- [模型布局和公共入口](model_layout.md)
- [M6–M8 日历设计](model7_model8_calendar_design.md)
- [数据及来源状态](../data/README.md)
- [现有实验与结果](../output/README.md)
- [原固定分批研究计划](static_single_multibatch_study_plan.md)

带日期的实验报告保留对应版本的采用决策、局限和失败结果；当前模型定义以模型目录及设计说明为入口。
