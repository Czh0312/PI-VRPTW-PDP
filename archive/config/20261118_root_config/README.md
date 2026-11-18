# 原根目录配置

这里保存原 `config/` 中的演示、实验登记、箱池、车队、作业规则和求解器预设。现有配置加载器支持原 `config/...` 路径，并按原规则解析文件中的 `$include`。[原实验登记](experiments/current.json)

`basetest` 按小、中、大规模维护车队配置，三个规模共用一份费率。日常运行通过 `--case`、`--model` 和运行预算参数选择算例和求解方式。[算例与配置位置](../../../data/basetest/README.md)

模型 3、4 的单批箱池由 `container_pool_size` 或容器比例配置指定。模型 5 使用 `joint_batch_options` 的箱池和日历允许域，模型 6 使用 `no_batch_options` 的箱池上限和目标。参数变更后的实验写入新的结果目录。

[旧编号配置](../../datasets/pre_renumbering_20261113/README.md)与旧结果一起保留，用于复现。
