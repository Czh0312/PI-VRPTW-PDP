# 历史生命周期成本分析

这里保留旧的整池更新、累计现金支出、回本周期与价格和寿命网格计算，以及 2026-10-05 重算脚本。重算脚本读取[当时的冻结费率](../config/20261007_before_classification/cost/cases/)。复查历史输出时应使用对应的冻结输入、求解结果与软件版本。当前每期成本比较见[单批对照](../../docs/single_batch_comparison.md)。

`archive.lifecycle_cost.multibatch` 使用旧费率和分摊参数复算已保存的单期路线。命令行可通过 `main.py cost` 传入旧结果和对应费率。[旧费率复算](multibatch.py)
