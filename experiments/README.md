# 实验入口

| 任务 | 入口 |
| --- | --- |
| 当前六模型单例 | `main.py route --case S03 --model N` |
| 按规模或编号批量运行 | `experiments.runs.run_current_benchmark --models 3 4 5 6 --sizes small --solver alns --seconds 10` |
| 单批四模型经济对照 | `main.py compare-single-batch` |
| 固定箱池方案扫描 | `main.py compare --case S03 --model N`，N 为 1–4 |
| 当前算例 ALNS 诊断 | `experiments.runs.profile_alns_evaluation` |
| 模型 4 强化表达对照复现 | `experiments.runs.run_model4_network_trial` 读取冻结的 v1 算例 |
| 模型 5 路线库与精确求解研究 | `experiments/runs/` 中的 `model5_*` 与相关脚本 |

实验预算、种子和证明范围随结果保存。新实验默认从 `basetest` 读取算例编号对应的 CSV 表和规模配置。旧实验按原有配置与编号阅读和复跑。[基础算例](../data/basetest/README.md) · [旧脚本](../archive/experiments/pre_renumbering_20261113/README.md) · [历史结果](../output/history/pre_renumbering_20261113/README.md)
