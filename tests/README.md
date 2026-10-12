# 回归测试

从仓库根目录运行 `make test` 做日常基础检查。测试依赖见根目录 `pyproject.toml` 的 `dev` 分组，
检查清单在 [Makefile](../Makefile)，分组执行见[开发指南](../docs/development.md)。

```sh
# 基础检查：M1–M8 入口、求解器隔离、周期审计、批次数域、费用和数据读取
make test

# 改动相关文件：指定文件时只运行这些测试
make test TESTS="tests/routing/test_model5_cycle_cache.py"

# 完整回归：穷举、参数组合、历史基线和完整数据集检查
make test-full
```

基础检查用于快速发现常见问题，不能代替改动对应的专项测试。
原生 `.venv/bin/python -m pytest -q` 仍执行完整回归，未设置隐式跳过规则。
可通过 `PYTHON` 选择解释器，通过 `PYTEST_ARGS="--durations=10"` 查看耗时。

| 范围 | 测试位置 |
| --- | --- |
| 模型分流与适用范围 | `routing/test_numbered_model_entries.py`、`routing/test_model_scope.py` |
| 精确 / ALNS 依赖隔离 | `routing/test_solver_isolation.py` |
| M6–M8 日历、K 域与独立参考枚举 | `routing/test_batch_count_domain.py`、`routing/test_model78_calendar.py`、`routing/test_model78_independent_oracle.py` |
| 单批 / 多批求解与证明边界 | `routing/test_single_batch_*.py`、`routing/test_multibatch_*.py` |
| 费用与生命周期 | `cost/` |
| 数据生成、清单与冻结源码恢复 | `data/` |

当前周期与数据验收：`routing/test_multibatch_cycle.py`、`data/test_static_batch_suite.py`、`routing/test_model_scope.py`。
历史 M1–M4 算法测试导入 archive.routing_baselines，证明仅适用于相应旧规则。
旧定义 M5–M8 的专属测试已随历史模型归档；当前同编号模型由现行多批与日历测试覆盖。

`routing/test_single_batch_comparison.py` 覆盖四模型任务差异、自由分仓修复不可行、未闭合枚举的证明边界、时间规范化、寿命补购、价格敏感性及冻结执行。

## 已归档实验测试

2026-10-07 将未被采用的 [M5 后悔值修复组件及其测试](../archive/experiments/model5_regret_repair/README.md)
一起归档，移出默认测试集的范围为 1 个测试文件、5 个测试函数、15 个参数化用例。
归档说明中保留手动测试命令和历史冻结源码位置。

清理前先核对对应功能是否仍在使用、是否有其他测试依赖其夹具、是否需要历史复现。
仍被当前入口或显式基线使用的行为测试继续保留；未采用的实验组件与专属测试一起归档。

## 控制验证成本

普通修改先运行受影响的测试文件；文档修改只检查相关链接与命令。
公共约束、独立审计、模型分流或跨模型重构需要扩大验证范围，完整回归按影响范围选择。
新增测试优先复用已有场景；新增随机种子或参数组合应说明补充了哪种边界，避免只增加重复运行。

2026-10-07 合并 M7/M8 固定日历穷举测试：两个模型仍分别求解，共用日历只计算一次参考最优值，
固定日历参考求解从 58 次减为 42 次。分仓证明边界测试改用局部受控时钟，并复用独立复制的
可行样例，继续检查未闭合分仓的重试与证明限制，无需反复求解直到耗满 2 秒。
