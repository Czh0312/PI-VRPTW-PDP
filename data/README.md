# 数据集入口

当前正式输入为 [formal_benchmark27_spacetime_v3](formal_benchmark27_spacetime_v3/README.md)：小规模9例均为10位顾客；中规模20位4例、30位5例，大规模50位4例、100位5例。每客一个标准箱，保留所选顾客全部货重，不进行装箱优化。旧 [spacetime_v2](formal_benchmark27_spacetime_v2/README.md)、[one_box_v1](formal_benchmark27_one_box_v1/README.md)、[多箱版本](formal_benchmark27_v1/README.md) 及冻结结果保留。新旧结果分别报告。

- **27个基础算例、216份模型配置**：model_1～model_8，各有small / medium / large，每档9例。
- 同一编号的需求、坐标、车队、绝对送取窗与适用费用保持一致；固定／优化项见各例parameters.json。
- 新版独立可行性见证见 [validation](formal_benchmark27_spacetime_v3/validation/README.md)。可行不等于算法运行成功或最优。
- 外部需求时段为2/3/4；M5使用固定参考，M6–M8仅剔除有不可行性证明的K。费用保留来源字节，本次不重新定价。
- 小规模9例来自6个原始来源；3对10位顾客子集各重叠8位顾客。中大规模也存在同源嵌套，不能当作27个独立来源。

[216项入口清单](formal_benchmark27_spacetime_v3/case_index.csv) · [小规模选例清单](formal_benchmark27_spacetime_v3/selection.json) · [旧版设计说明](../docs/benchmark27_design_20261015.md)

## 目录整理

`data/` 保留当前输入及各历史正式版本。旧静态场景、模型专项版本、原始／派生来源以及历史实验输入统一迁至 [历史数据归档](../archive/datasets/README.md)，不并入当前27例统计。

本轮迁移6,127个文件，逐文件SHA-256核验一致；此前三个退役版本的983个文件仍保存在独立ZIP中。历史JSON中的原路径与来源哈希保留，项目加载器兼容解析这些旧引用。正式数据运行不依赖归档；重新生成正式数据时会从归档读取并校验原始选例来源。

旧生成工具默认写入 `output/generated_datasets/`，避免再次混入主数据目录。历史数据规则见 [旧版说明](../archive/datasets/legacy_20261015/data/LEGACY.md)。
