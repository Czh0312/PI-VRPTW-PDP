# 公用工具

| 目录 | 职责 |
| --- | --- |
| `configuration/` | 公用参数读取、合并、路径解析、容器池设置与输入冻结 |
| `data/` | 数据生成、清单选择、冻结源码和大文件恢复 |
| `audit/` | 物理审计、求解器诊断、配对性能检查与报告 |
| `reporting/` | 公用路线绘图；`route_plot.py` 生成图片到 `output/` 对应实验目录 |

通常从仓库根目录使用 `.venv/bin/python -m tools.data.脚本名 --help` 或 `tools.audit.脚本名 --help` 查看参数。
批量研究入口在 [`experiments/`](../experiments/README.md)，开发与复现说明见[开发指南](../docs/development.md)。

`data/restore_benchmark_sources.py` 校验 / 恢复 M1–M4、M5 的共享冻结源码；
`data/restore_model6_sources.py` 校验 / 恢复 M6 的共享版本；
`data/restore_large_artifacts.py --verify` 校验分片存储的大文件。恢复前由各工具核对哈希，使用方法见开发指南。

## 数据构造与算法诊断

用 `python -m tools.audit.audit_current_benchmark` 校验当前 27 张算例表及 162 个模型组合。`data/build_shared_benchmark.py` 用于从冻结的 v1 输入重建归档的 v2 格式。
旧数据导入、候选案例构造和旧批量实验工具见[历史脚本](../archive/experiments/pre_renumbering_20261113/README.md)。

定制精确算法的专用测评与证书复核脚本已移入[研究归档](../archive/exact_research_20261019/README.md)。
当前精确求解统一使用直接 Gurobi；保留的数据审计、模型对比、ALNS 诊断与结果汇总工具照常使用。
