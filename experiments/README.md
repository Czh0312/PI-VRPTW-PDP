# 实验

正式入口为 `runs/run_multibatch_cycle.py`，汇总在 `analysis/summarize_multibatch_cycle.py` 与 `analysis/summarize_static_multibatch.py`。
旧单日 runner 保留用于显式历史基线，不能替代新版 B=1。
其余旧模型实验已归档，见 `../archive/cleanup_20260929/`。

`runs/run_single_batch_comparison.py` 是新版四模型 B=1 与条件经济对照入口，支持冻结输入、独立外层超时、全部失败留存和按方法/种子分别核算。
