# 数据集入口

当前正式输入为 [formal_benchmark27_one_box_v1](formal_benchmark27_one_box_v1/README.md)：每客一个标准箱，保留全部货重，不进行装箱优化。原 [formal_benchmark27_v1](formal_benchmark27_v1/README.md) 及冻结结果保留为“多箱需求情景”。新旧结果分别报告，验证与重跑状态见[版本说明](../docs/benchmark27_one_box_20261019.md)。

- **27个基础算例、216份模型配置**：model_1～model_8，各有small / medium / large，每档9例。
- 同一编号的需求、坐标、车队、绝对送取窗与适用费用保持一致；固定／优化项见各例parameters.json。
- 两个版本分别保存独立可行性见证；见新版本 [validation](formal_benchmark27_one_box_v1/validation/README.md)。可行不等于算法运行成功或最优。
- 固定参考日历统一为三批、70%分界；M6–M8仅剔除有不可行性证明的K。主数据箱池费仍为每只每周期5个合成单位。
- 27例来自14个来源块，中大规模存在嵌套，不能当作27个独立来源。

[216项入口清单](formal_benchmark27_one_box_v1/case_index.csv) · [一客一箱说明](../docs/benchmark27_one_box_20261019.md) · [旧版设计说明](../docs/benchmark27_design_20261015.md)

## 目录整理

`data/` 保留本说明及新旧两个正式输入版本。旧静态场景、模型专项版本、原始／派生来源以及历史实验输入统一迁至 [历史数据归档](../archive/datasets/README.md)，不并入当前27例统计。

本轮迁移6,127个文件，逐文件SHA-256核验一致；此前三个退役版本的983个文件仍保存在独立ZIP中。历史JSON中的原路径与来源哈希保留，项目加载器兼容解析这些旧引用。正式数据运行不依赖归档；重新生成正式数据时会从归档读取并校验原始选例来源。

旧生成工具默认写入 `output/generated_datasets/`，避免再次混入主数据目录。历史数据规则见 [旧版说明](../archive/datasets/legacy_20261015/data/LEGACY.md)。
