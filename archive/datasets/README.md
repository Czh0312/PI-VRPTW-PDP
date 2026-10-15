# 历史数据归档

现行主数据集只有 [formal_benchmark27_v1](../../data/formal_benchmark27_v1/README.md)：八模型、小中大各九例。`data/` 仅保留这一正式目录和入口说明。

## 仍需复现的历史数据

[legacy_20261015/data](legacy_20261015/data/) 集中保存原有静态数据、model6、model5_model6、来源底稿及历史实验输入，共6,127个文件、27,270,766字节。移动前后逐文件SHA-256一致；[迁移清单](legacy_20261015/migration.json)记录原路径、新路径、大小和校验值。原始下载数据的Git忽略规则同步保留。

这些数据用于旧测试、历史复现和正式数据的来源校验，不是新增主实验算例。原JSON、来源哈希及历史报告未改写。项目的 `resolve_project_path` 优先读取现行路径，仅在缺失时解析已有归档；CSV公共读取入口也支持旧路径。直接使用文件系统的外部脚本应使用新路径。旧数据的详细规则见 [LEGACY.md](legacy_20261015/data/LEGACY.md)。

旧生成工具默认输出到 `output/generated_datasets/`；如需复建可显式指定临时目录，避免覆盖归档。正式数据的运行输入均在正式目录内，只有重新生成时需要读取归档来源。

## 已退役的三个版本

`retired_20261015.zip` 保存此前清理的 cutoff_v1_20261005、static_v2_full_20261004、static_v2_lade_20261004，共983个文件。完整清单见 [retired_20261015.json](retired_20261015.json)。

只有复现这些退役版本时才运行 `.venv/bin/python -m tools.data.retire_unused_datasets --restore`；该命令会恢复原 `data/model6/` 路径，先验证全部内容并拒绝覆盖不同文件。通常无需恢复。
