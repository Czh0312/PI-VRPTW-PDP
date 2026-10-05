# 回归测试

运行 `.venv/bin/python -m pytest -q`。

当前周期与数据验收：test_multibatch_cycle.py、test_static_batch_suite.py、test_model_scope.py。
历史 M1–M4 算法测试导入 models.routing.baselines，证明仅适用于相应旧规则。
旧 M5–M8 专属测试已随模型归档。

`routing/test_single_batch_comparison.py` 覆盖四模型任务差异、自由分仓修复不可行、未闭合枚举的证明边界、时间规范化、寿命补购、价格敏感性及冻结执行。
