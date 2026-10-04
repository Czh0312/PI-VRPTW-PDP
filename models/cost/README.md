# 费用层

`multibatch.py` 对新版已审计路线核算周期运营费用，区分一次性购买与循环清洗/追踪，并单列箱池投入。
`single_batch.py` 对 M1/M3、M2/M4 核算重复周期、整池寿命补购、累计回本和价格/寿命敏感性。
`lifecycle.py`、`parameters.py`、`metrics.py` 保留通用旧算术，不作为新版四模型执行入口。

详见 [单批经济说明](../../docs/single_batch_comparison.md)。费用是距离方案的后处理，不是费用最优解。
旧六模型成本入口和旧回本报告已归档。当前 synthetic_units 参数仅用于演示，真实盈利尚未验证。
