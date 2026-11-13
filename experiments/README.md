# 实验入口

| 任务 | 入口 |
| --- | --- |
| 当前六模型单例 | `main.py route --model N --case <case.json>` |
| 按规模或编号批量运行 | `experiments.runs.run_current_benchmark --models 3 4 5 6 --sizes small --solver alns --seconds 10` |
| 单批四模型经济对照 | `main.py compare-single-batch` |
| 固定箱池方案扫描 | `main.py compare`，使用模型 1–4 的输入 |
| 当前算例 ALNS 诊断 | `experiments.runs.profile_alns_evaluation` |
| 模型 4 强化表达对照 | `experiments.runs.run_model4_network_trial` |
| 模型 5 路线库与精确求解研究 | `experiments/runs/` 中的 `model5_*` 与相关脚本 |

实验预算、种子和证明范围随结果保存。新实验使用 [162 个配置的算例索引](../data/benchmark27_models_1_6_v1/case_index.csv)。旧实验按原有配置与编号阅读和复跑。[旧脚本](../archive/experiments/pre_renumbering_20261113/README.md) · [历史结果](../output/history/pre_renumbering_20261113/README.md)
