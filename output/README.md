# 正式成果

本目录是唯一成果入口，按实验主题保存正式结果、图片、报告和 Excel。
实验程序在 `experiments/`，实验参数在 `config/experiments/`。
与结果配套的冻结输入、源码、原始方案和审计用于复现，随对应实验保存。
当前正式输入为 `formal_benchmark27_spacetime_v3`，小规模9例统一10位顾客；见[新版数据说明](../data/formal_benchmark27_spacetime_v3/README.md)。输入修订和新一轮求解分别记录。旧 `spacetime_v2`、`one_box_v1`、多箱版本及其冻结结果保留，下列既有结果按各自冻结输入保留，不能直接作为新情景结论。

## 主要入口

| 研究内容 | 报告入口 | 原始目录 |
| --- | --- | --- |
| M6–M8 有限时间求解改进（选例诊断） | [成对结果与限制](inspections/solver_improvement_20261017/report.md) | `inspections/solver_improvement_20261017/` |
| 修订3成本参数、八模型精确/五种子ALNS重跑 | [运行报告与结果](experiments/benchmark27_20261016_cost_update_v1/report.md) | `experiments/benchmark27_20261016_cost_update_v1/` |
| 正式27例八模型、精确/五种子ALNS重跑 | [实时报告与最终结果](experiments/benchmark27_20261015_formal_v1/report.md) | `experiments/benchmark27_20261015_formal_v1/` |
| M1–M4 精确 / ALNS 全规模对比 | [实验说明](experiments/m1_m4_comparison_20261004/m1_m4_solver_comparison_20261004.md) | `experiments/m1_m4_comparison_20261004/` |
| M5 精确 / ALNS 全规模补测 | [实验说明](experiments/m5_comparison_20261004/m5_solver_comparison_20261004.md) | `experiments/m5_comparison_20261004/` |
| M6 分批、箱池与成本研究 | [结果索引](model6_comparison/README.md) | `model6_comparison/` |
| M3/M5/M6/M7/M8 成本对比 | [历轮结果索引](model35678_comparison/README.md) | `model35678_comparison/` |
| M5–M8 同成本对比 | [机制例](model5678_comparison/20261006_same_cost_v1/report.md)、[较大算例](model5678_comparison/20261006_large_same_cost_v1/report.md) | `model5678_comparison/` |
| 经济性重新核算 | [报告](economic_recalculation_20261005/report.md) | `economic_recalculation_20261005/` |
| 算法开发与诊断 | [文档导航](../docs/README.md)、[历次实验导读](../archive/docs/README.md) | `experiments/` |
| 文献核对 | [研究重叠与证据审查](literature_audit_20261004/novelty_overlap_audit_20261004.md) | `literature_audit_20261004/` |
| 单次命令输出 | [运行方式](../README.md) | `single_runs/` |
| 分片保存的大文件 | [恢复说明](large_artifacts/README.md) | `large_artifacts/` |

## 使用方式

先阅读相应报告的配置、预算、种子和证明范围，再查看保存的方案、日志与独立审计。新价格下重计价和重新求解分别保留，历史可行方案不自动代表当前最优或实际经济收益。

冻结材料保持原路径和字节；已有哈希清单及恢复工具依赖它们。部分逐次源码副本由经过校验的共享树恢复，见[开发与复现](../docs/development.md)。新实验使用新的输出目录。

`.ignore` 让默认源码检索跳过本目录；显式查结果可用 `rg --no-ignore '关键词' output/目标实验目录/`。Git 继续跟踪原有结果。

Excel 交付文件已归入对应实验：[M1–M4 模型对比](experiments/m1_m4_comparison_20261004/M1-M4模型对比.xlsx)、
[经济可行性重新核算](economic_recalculation_20261005/经济可行性重新核算.xlsx)。
一次性调试和预览使用系统临时目录，不另建 `outputs/` 或按聊天编号存放成果。
2026-09-29 清理前的旧模型结果保存在 `archive/cleanup_20260929/before_cleanup.tar.gz`；后续显式历史基线运行使用 `output/baselines/`。

- [精确求解模型压缩验证](inspections/exact_compaction_20261017/report.md)：M6–M8 的等价模型压缩、初值策略及同预算结果；历史试跑与最终验证分别保存。
