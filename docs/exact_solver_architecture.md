# 精确求解架构与结果契约

本项目严格区分“找到可行解”和“证明原问题全局最优”。所有实验汇总都应以 `proven_optimal` 为最终判据。

## 统一目录

Model 1–7 的精确模块结构一致：

```text
models/routing/variants/model_N/
  model.py          当前完整数学模型
  exact_solver.py   对应的严格精确入口
```

Model 1 和 Model 3 因公式组件较多，另有 `formulation/`；这只是完整模型的必要拆分，不是备选求解算法。

## 统一结果字段

精确入口在 `return_info=True` 时返回路线和 `info`。至少检查：

| 字段 | 含义 |
| --- | --- |
| `status` | 求解器或前置证书的状态 |
| `objective` | 当前可行解目标值；没有 incumbent 时为空 |
| `best_bound` | 当前有效下界 |
| `mip_gap` | 上下界差距 |
| `solution_type` | 精确、限时可行、受限模型等结果语义 |
| `proven_optimal` | 是否已证明原问题全局最优 |

此外，Model 1–4 的实验与 CLI 会对提取后的路线执行独立审计。审计器不读取
Gurobi 变量，而是从路线重新检查任务覆盖、分仓、车辆数、载重/体积、时间窗、
先送后取、E1–E2 衔接，并重新计算总距离。正式结果还必须满足
`route_audit.valid=True` 且 `route_objective_delta` 在数值容差内。

时间限制下得到的 incumbent 可以作为可行上界，但必须标记为 `feasible_not_proven` 或等价状态。

## 各模型的证明条件

| 模型 | 当前精确方法 | `proven_optimal=True` 的条件 |
| --- | --- | --- |
| M1 | 完整固定分仓纯配送弧流 MIP | 完整 MIP 返回 `OPTIMAL` |
| M2 | 完整自由分仓纯配送弧流 MIP | 完整 MIP 返回 `OPTIMAL` |
| M3 | 完整固定分仓配送—回收联合弧流 MIP | 完整 MIP 返回 `OPTIMAL` |
| M4 | 完整自由分仓配送—回收弧流 MIP | 完整 MIP 返回 `OPTIMAL`；完整分仓枚举仅在全部分区闭合时可认证 |
| M5 | 严格松弛证书，必要时回退完整单次接力联合 MIP | 有效下界与可行上界闭合，或完整联合 MIP 返回 `OPTIMAL` |
| M6 | 严格松弛证书，必要时回退紧凑同质空箱联合 MIP | 有效下界与可行上界闭合，或完整紧凑 MIP 返回 `OPTIMAL` |
| M7 | 完整顾客—前置仓分区枚举；每个分区求解 M6 完整联合 MIP | 所有互斥分仓分区均被证明最优或不可行，且最优可行分区已确定 |

M5/M6 的有限近邻候选、固定路线二阶段优化或其他缩减候选只能作为受限结果，除非另有覆盖完整可行域的严格证明。M7 只要有一个分仓分区未闭合，就只能报告限时 incumbent，不能报告联合模型全局最优。

## 历史算法的位置

路线宇宙、受限路线池、列生成、分支定价、时间扩展、拉格朗日分解和研究探针均位于：

```text
archive/models/routing/variants/
archive/experiments/
archive/reference/README.md
```

它们用于追溯研究过程，不参与当前精确入口，也不应被当前结果表当作全局证明。

## 回归检查

当前精确功能回归位于：

```text
tests/routing/test_model1234_exact.py
tests/routing/test_model5_exact.py
tests/routing/test_model6_exact.py
tests/routing/test_model7_exact.py
```

`test_model1234_exact.py` 会分别调用四个完整 MIP，核对最优证书、自由分仓支配
关系和独立路线审计，并确认被破坏的路线能够被审计器拒绝。

运行全部功能测试：

```powershell
python -m pytest -q
```

没有 Gurobi 或许可证时，依赖 Gurobi 的测试会跳过。修改模型或结果状态映射后，应同时检查：可行路线、目标值、下界、gap、`solution_type` 和 `proven_optimal`，不能只检查求解器是否返回了路线。
