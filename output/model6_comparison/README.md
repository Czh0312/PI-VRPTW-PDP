# 模型6实验发布与源码恢复

本目录保留机制例、LaDe静态场景、全部186个V2多批次配置、两窗截点和均匀周期对照的输入、结果、失败与限时记录、审计及汇总。实验预算和解释限制以各目录的 `experiment.json` 与报告为准；费用为合成单位，不代表已校准人民币节约。

主要结果：

- [全部V2多批次配置](20261004_static_v2_full/full_summary.md)
- [较大LaDe场景箱数与批次](20261004_lade_static_v2/resources.md)
- [机制例最终结果](20261004_mechanism_v1_final/report.md)
- [两窗截点对照](20261005_cutoff_v1/cutoff_report.md)
- [均匀周期最终结果](20261005_uniform_v1_final/uniform_report.md)
- [均匀周期与模型5对照](20261005_uniform_v1_final/comparison_to_model5.md)

2488份逐次运行清单的 `source_sha256` 对应6个源码版本。发布时逐文件校验原有逐次副本，再把每个版本保存到 `shared_sources/<指纹>/`；`source_snapshot_index.json` 保存运行目录到源码版本的映射及各文件SHA-256。逐次重复的 `source_snapshot/` 留在本地，Git中通过共享版本完整恢复。各实验根目录原有 `source_snapshot/` 仍保存，其中还包含当时的运行器、分析与工具文件。已有运行清单、输入和结果不改写。

校验共享源码、全部运行清单映射及现存逐次副本：

```sh
.venv/bin/python -m tools.data.restore_model6_sources
```

新检出仓库可恢复一个实验或一次运行的逐次源码：

```sh
.venv/bin/python -m tools.data.restore_model6_sources --restore --study 20261005_uniform_v1_final
.venv/bin/python -m tools.data.restore_model6_sources --restore --run 20261004_static_v2_full/99_M5_distance_alns_43
```

省略筛选参数会恢复全部逐次副本，占用约3.8GB。工具仅补齐缺失文件；现存源码与清单哈希不一致时拒绝继续。恢复完成后可按原运行清单核对当时的源码；不同历史实验可能使用不同版本。

新增实验的全部逐次源码保留完整时，可重新构建并校验发布索引：

```sh
.venv/bin/python -m tools.data.restore_model6_sources --publish
```
