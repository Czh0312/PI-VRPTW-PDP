# 参数配置

[当前实验登记](experiments/current.json)指向六模型基准数据和算例索引。`experiments/examples/` 提供模型 3–6 的 S01 入口。

配置中的 `$include` 按仓库根目录解析。后载入的值覆盖前面的值，当前文件及命令行的显式参数优先。各基础算例的 `shared/` 保存路网、车队和费用，模型目录保存作业规则及订单。

模型 3、4 的单批箱池由 `container_pool_size` 或容器比例配置指定。模型 5 使用 `joint_batch_options` 的箱池和日历允许域，模型 6 使用 `no_batch_options` 的箱池上限和目标。参数变更后的实验写入新的结果目录。

[旧编号配置](../archive/datasets/pre_renumbering_20261113/README.md)与旧结果一起保留，用于复现。
