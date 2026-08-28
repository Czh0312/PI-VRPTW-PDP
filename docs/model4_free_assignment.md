# Model 4：自由分仓配送与延后回收

## 定位

Model 4 在 Model 3 的配送—延后回收网络上，把顾客归属哪个前置仓从输入参数改为决策变量。同一顾客的配送和回收仍归属同一个前置仓。

M3 的任何固定分仓可行解都属于 M4 的可行域，因此在相同参数和完整求解口径下，M4 的最优值不应劣于 M3。

## 完整数学模型

`models/routing/variants/model_4/model.py` 保存当前完整自由分仓弧流 MIP。模型联合决定：

- 顾客—前置仓分配；
- 前置仓是否启用；
- E1 配送与回收路线；
- E2 顾客配送与延后回收路线；
- 车辆容量、时间窗、先送后取和 E1/E2 同步。

前置仓的配送量、回收量和配送截止时间由当前分配动态产生。E1 回收可以早到等待，但实际回收时刻不得早于该仓相关 E2 回收任务返回完成时刻。

## 精确求解

`models/routing/variants/model_4/exact_solver.py` 是当前严格入口。完整 MIP 返回 `OPTIMAL` 时可认证全局最优；完整分仓枚举只有在全部互斥分区都闭合后才可认证。

模型规模随顾客和候选前置仓组合快速增长，因此精确求解主要用于 Zhou 小规模实例的模型验证。限时 incumbent 应保留 objective、best bound 和 gap，但不能标成精确最优。

历史受限路线池、两状态时间扩展、单仓程序和分支定价尝试已归档：

```text
archive/models/routing/variants/model_4/
archive/experiments/
```

## ALNS

`models/routing/alns/variants/model_4.py` 实现分配感知 ALNS：

- 顾客配送/回收任务成对删除，保持同仓分配；
- 修复时跨前置仓和车辆比较可行插入；
- 分配变化后重建 E1 配送量、回收量和截止时间；
- 保留可行 E1 结构并进行局部改进；
- 输出路线、分配、搬移顾客数和成本。

M4 单次迭代通常比 M3 更重，公平对比应使用相同时间预算，而不是只固定迭代次数。

## 当前精确强化

Model 4 支持可选的等价强化配置，例如时间不可行弧裁剪、逐弧安全 Big-M、冗余先后约束消除、路线对称性处理和分仓优先分支。它们不改变完整可行域。

当前 A/B 入口：

```powershell
python experiments/runs/run_m4_exact_acceleration.py `
  --dataset data/zhou_later_pickup/small/standard_same_day/Set1_12-2-4.csv `
  --time-limit 120
```

小规模完整分仓枚举备用入口：

```powershell
python experiments/runs/run_m4_assignment_decomposition.py `
  --dataset data/zhou_later_pickup/small/standard_same_day/Set1_7-2-1b.csv `
  --total-time-limit 600
```

## 统一入口

```powershell
python main.py route --case config/cases/zhou_small_demo.json `
  --model 4 --solver alns --time 30
```

当前实验只使用 Zhou later-pickup 数据；旧 Solomon/北京结果属于历史研究材料，不进入当前汇总。
