# 费用层

`multibatch.py` 对新版已审计路线核算周期运营费用，区分一次性购买与循环清洗/追踪，并单列箱池投入。
`single_batch.py` 对 M1/M3、M2/M4 核算重复周期、整池寿命补购、累计回本和价格/寿命敏感性。
`lifecycle.py`、`parameters.py`、`metrics.py` 保留通用旧算术，不作为新版四模型执行入口。

详见 [单批经济说明](../../docs/single_batch_comparison.md)。费用是距离方案的后处理，不是费用最优解。
旧六模型成本入口和旧回本报告已归档。synthetic_units 参数仅用于演示，真实盈利尚未验证。

2026-10-05 重新核算入口为 `experiments/analysis/recalculate_economics.py`：
LaDe 杭州 V2 的 21 组基础案例使用公里/分钟矩阵和人民币参考参数；
2026-10-04 全量 Zhou 保存结果仅按 synthetic_units 计价。数据集与单位不可混用。
结果与复现方法见 [本轮经济核算](../../output/economic_recalculation_20261005/economic_recalculation_20261005.md)。
