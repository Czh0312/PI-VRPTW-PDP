# M4/M2 自由分配模型的列生成诊断（2026-08-17）

## 结论

**当前 M4/M2 的 route-column master 不能用“coverage + vehicle dual”的 pricing 做完整列生成证明。**
相关 `solve_m*_column_generation` 入口已保留为 deprecated diagnostic，并在运行时抛错，避免再次误标
`proven_optimal=true`。

原因：完整路线列 master 中，`y_route` 不只出现在覆盖约束和车辆数约束，还出现在：

- E2 route timing：`E2_RouteTime_*`、`E2_ReturnLo_*`；
- route 使用约束：`E2_StartUsed_*`、`E2_ReturnUsed_*`；
- E1/E2 衔接：`Link_Start_*`、`Link_Pickup_*`。

这些约束在对偶侧同样贡献到 `y_route` 的 reduced cost。当前 pricing 公式

```text
rc(route) = route_cost - Σ_c π[c,s] - σ_s
```

只取了覆盖对偶 `π[c,s]` 和车辆数对偶 `σ_s`，因此不是完整 master 的列 reduced cost。

## 实测证据

在 LP 最优解中直接读取 Gurobi 的 `y_route.RC`，与 pricing 公式比较：

- 10c：最大误差 `40.4466`（Gurobi RC 为 0，公式 RC 为 40.45）；
- 50c：最大误差 `133.1246`。

因此：

- LP 未变化不代表无负 reduced-cost 列；
- “CG converged” 不能说明列池 LP-complete；
- 在该列池上 Gurobi `OPTIMAL` 只能称为 restricted route-pool optimum；
- 不能据此把 M4/M2 记作全局 `proven_optimal`。

## 数学上有效的松弛下界实验

将所有含 `y_route` 的路线时序/衔接约束从 LP 中移除，仅保留 E1 侧、覆盖、车辆数约束。此时
pricing 公式与 LP 对偶完全一致；实测 Gurobi `RC` 与公式误差为 `1.4e-14`，可做合法列生成下界。

但在 C101/50c 上收敛极慢：

| 迭代 | LP 下界 | 备注 |
|---:|---:|---|
| 0 | 1126.8157 | 初始列池 |
| 6 | 1109.7567 | 开始缓慢下降 |
| 12 | 1072.1008 | |
| 21 | 1053.7225 | |
| 24 | 1053.7225 | 约两小时后仍未收敛 |

已知 50c restricted-pool incumbent 为 `1285.1435`，合法下界远未闭合。该路径继续跑完也无法在
合理时间内成为实用 50c 精确证书。

## 当前正确口径

- `output/**/m4_cg_result.json`、`m2_cg_result.json` 已标记为 deprecated diagnostic，
  `proven_optimal=false`。
- M4/M2 现有 50c/100c “最优值”只能称为 **restricted route-pool optimum**（在该 singleton + ALNS
  + 生成的列池内最优），不是全局精确证明。
- 若要恢复精确证明，需要满足以下之一：
  1. 证明所用列池包含所有可能进入最优解的 E2 路线（complete route universe certificate）；
  2. 在完整 master 下实现包含所有相关对偶的 pricing，或重新做 Dantzig-Wolfe 分解使耦合约束进入
     master、E2 路线子问题自包含；
  3. 对给定 incumbent 构造独立的全局下界证书并闭合 gap。
