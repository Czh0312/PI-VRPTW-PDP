# experiments 目录说明

当前正式数据已经切换到 `data/zhou_later_pickup/`。

## 当前入口

- `run_mainline_small_exact.py`：读取 `small/<scenario>/`，运行 M1/M2/M3/M4
  小规模精确实验。
- `run_m4_exact_acceleration.py`：通过 `--dataset` 接收单个 Zhou 小算例。
- `run_m4_assignment_decomposition.py`：通过 `--dataset` 接收单个 Zhou 小算例。

## 历史脚本

目录中仍有一批脚本包含 `5_customers`、`C101`、`R101` 或 `beijing_fresh`
等旧路径。这些脚本保存算法研究过程，但其数据已经删除，不能直接运行。它们需要
逐个迁移为 Zhou 的 `small/large + scenario + Set1/Set2` 选择方式后，才能重新进入
当前实验主线。不要通过随意把 C101 映射成某个 Set1/Set2 实例来修复。

