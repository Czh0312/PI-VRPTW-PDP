# 费用层

`joint_batch.py` 提供 M1–M6 共用的每期费用公式。M1、M2 的纸箱按当期消耗量计，M3–M6 的循环箱按实体箱池和单箱日费计。MILP、ALNS 和独立审计使用同一费用配置。

`multibatch.py` 和 `single_batch.py` 支持方案的事后费用及生命周期分析，包括整池寿命补购、累计回本和价格敏感性。详见 [每期费用](../../docs/model3_cost_comparison.md) 和 [单批经济说明](../../docs/single_batch_comparison.md)。
`lifecycle.py`、`parameters.py`、`metrics.py` 保留通用旧算术，不作为新版四模型执行入口。

旧六模型成本入口和旧回本报告已归档。`synthetic_units` 为演示算例的模拟单位。

2026-10-05 重新核算入口为 `experiments/analysis/recalculate_economics.py`：
LaDe 杭州 V2 的 21 组基础案例使用公里/分钟矩阵和人民币参考参数；
2026-10-04 全量 Zhou 保存结果仅按 synthetic_units 计价。数据集与单位不可混用。
结果与复现方法见 [本轮经济核算](../../output/economic_recalculation_20261005/economic_recalculation_20261005.md)。
