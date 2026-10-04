# 实验

正式入口为 `runs/run_multibatch_cycle.py`，汇总在 `analysis/summarize_multibatch_cycle.py` 与 `analysis/summarize_static_multibatch.py`。
旧单日 runner 保留用于显式历史基线，不能替代新版 B=1。
其余旧模型实验已归档，见 `../archive/cleanup_20260929/`。

`runs/run_single_batch_comparison.py` 是新版四模型 B=1 与条件经济对照入口，支持冻结输入、独立外层超时、全部失败留存和按方法/种子分别核算。

`runs/run_single_batch_benchmark.py` 运行当前 M1–M4 的精确/ALNS 全量规模对比。
`analysis/summarize_single_batch_models.py --output <实验目录>` 读取其已保存结果，生成四模型横向对比，
列出 M2 相对 M1、M4 相对 M3 的距离减少量和减少率。使用 `--watch` 可在求解期间持续更新，
全部求解完成后自动收尾。精确可行解、ALNS 三种子最好和三种子均值分开报告；
汇总采用同一组四模型完整可行案例，并保留负减少率及缺失状态。

`runs/run_model5_benchmark.py` 补测 M5 的 B2/B3 精确算法与独立 ALNS，默认覆盖 140 个多批次配置、560 次运行。
支持 `--prepare-only` 冻结登记、`--resume` 校验快照后恢复，以及 `--after-progress` 等待前一轮实验完成。
逐规模和批次数分别输出 `runs.csv`、`comparison.csv`、`summary.csv` 与动态 `report.md`。
正式预算与复现说明见 [M5 全规模补测](../docs/m5_solver_comparison_20261004.md)。
