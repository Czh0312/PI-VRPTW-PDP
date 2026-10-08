# M5 后悔值修复实验（已归档）

2026-10-07 从当前源码与默认回归测试中一起归档。第六轮实验的三个候选均未通过
开发质量门槛，当前求解入口没有调用此组件；详见
[第六轮撤回记录](../../../docs/model5_alns_round6_20261001.md)。

| 文件 | 原位置 | 用途 |
| --- | --- | --- |
| `multibatch_repair.py` | `models/routing/solvers/alns/multibatch_repair.py` | 有界后悔值修复候选 |
| `test_model5_regret_repair.py` | `tests/routing/test_model5_regret_repair.py` | 5 个测试函数、15 个参数化用例，检查容量反例、截止中断、随机状态、可复现性和缓存等价性 |

迁移只调整了两处导入路径，算法和测试断言保持原样。没有其他当前测试依赖本文件的夹具。
这些用例仍有实验复核价值，因此与组件一起保留；默认 `pytest` 只收集 `tests/`。

## 手动检查归档组件

从仓库根目录运行：

```sh
.venv/bin/python -m pytest -q archive/experiments/model5_regret_repair/test_model5_regret_repair.py
```

本目录依赖当前公共模型、解码器和邻域实现，手动测试用于检查归档组件与当前依赖的兼容性。
若要重新接入当前求解器，应重新验证整体求解质量，并将相应测试恢复到默认测试集。

## 历史实验复现

原始候选源码、各自测试、哈希清单与实验结果保留在
[第六轮实验目录](../../../output/experiments/model5_alns_round6_20261001/)，
归档时已核对三个候选的 `freeze.json`，源码和测试哈希均一致。
严格复现应使用该目录的 `candidate_01/02/03/source` 冻结源码及
[原报告中的复跑命令](../../../docs/model5_alns_round6_20261001.md#复核与复现)。

原实验目录中的 `freeze_candidate.py`、`check_option_cache.py` 和 `save_counterexample.py`
保留了实验当时的工作区路径与候选状态假设，不是当前工作区的维护入口。
它们及已生成的历史结果保持原样；复核本次归档组件使用上面的显式测试命令。
