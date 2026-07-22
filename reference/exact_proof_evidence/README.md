# 精确求解证明证据（curated）

本目录固化 2E-VRPTW-PDD 精确求解研究的**不可替代证据**：已证明结果的关键 JSON
与复现 1829 路线池主问题所需的合并列池。完整的实验代码、Gurobi 日志和 per-run
列转储见研究 worktree（分支 `codex/exact-m2-strengthening` 与
`worktree-lagrangian-m2`，研究日志见其 `experiments/exact_worktree_progress.md`）。

`output/` 在仓库中被 gitignore（因含大量可重新生成的日志），因此这里只保留
**重新生成成本高或无法从代码直接推断**的小体量证据。

## 已证明结果总览

| 算例 | 结果 | 状态 |
| --- | --- | --- |
| Model 1 `100_customers/C101` | 1622.92 | Gurobi OPTIMAL（proven_optimal） |
| Model 2 `10_customers/C101` | 543.09 | Gurobi OPTIMAL |
| Model 2 `50_customers/C101` | 1289.06 | Gurobi OPTIMAL（需 S5 branch-price 下界注入后弧流模型 20.9s 闭合） |
| Model 2 `100_customers/C101` 全部 8 个 E2 卫星 | 见下 | 各卫星独立 BCP，均 OPTIMAL，和 1031.8799 |
| Model 2 `100_customers/C101` 受限列池主问题 | 1829.0094 | Gurobi OPTIMAL（仅对该 16,336 列池，**非**全路线宇宙） |
| Model 2 `100_customers/C101` 全模型 | incumbent 1829.0094 / bound 1824.2267 / gap 0.2615% | **未**由 Gurobi 证明；2026-07-03 有一份手写严格闭合定理声称其为全局最优（见研究日志），但按本项目口径不计入 `proven_optimal` |

口径提醒：只有 Gurobi 在**未缩减**模型上返回 `OPTIMAL` 才记为精确解
（`proven_optimal=True`）。受限列池 OPTIMAL、单分支节点闭合、限时 incumbent 均不认证。
详见 `docs/exact_solver_architecture.md`。

## 文件说明

### `exact_incumbents/`

- **`m2_100_c101_satellite_bcp_exact_lbs.json`** / **`..._runner.json`**
  Model 2 `100_customers/C101` 全部 8 个卫星的独立 branch-cut-and-price 最优值，
  全部 `OPTIMAL`：

  ```
  S0 180.69619741  S1 198.21968577  S2 117.48715889  S3 163.61709733
  S4  88.55002792  S5 107.98412711  S6 139.70141299  S7  35.62421378
  合计 = 1031.8799212028766
  ```

  这是 E2 部分的有效下界，也等于 1829.0094 incumbent 的 E2 代价，说明残余 0.2615%
  gap **不在 E2**，而在 E1↔E2 配送耦合（incumbent E1 797.13 vs E1 独立 LB 792.35）。

- **`m2_50_c101_1291.routes.json`**
  Model 2 `50_customers/C101` 的 warm-start 路线解（E1 + 各卫星 E2 路线），代价
  1291.4011108687314。注入弧流 Model 2 后模型在 20.9s 内闭合到 OPTIMAL。

- **`m2_50_c101_e2pdp300_best_lb.json`**
  50/C101 各卫星 PDP 下界（S0/S2/S5/S7，每卫星 300s）。S5 在该模式下 TIME_LIMIT
  gap 9.76%，是其后被 route-CG / branch-price 加强的动机。

- **`m2_50_c101_e2routecg_s5_best_lb.json`**
  S5 route-CG LP 下界 209.81088628437502（`lp_valid`、pricing OPTIMAL、reduced-cost
  bound ~-3.3e-12）。把 S5 下界从 PDP 的 194.98 抬到 209.81，使弧流 gap 从 1.75%
  降到 0.60%。

- **`m2_50_c101_e2routecg_s5_branchprice_ip_lb.json`**
  S5 完整 Ryan-Foster branch-price 证明：整数最优 215.1812629011442，
  `complete=true`、`remaining_queue=0`、`open_unproved_nodes=0`。S5 闭合后，弧流
  Model 2 `50_customers/C101` 立即 OPTIMAL。

### `merged_columns/`

- **`100_customers__C101__columns.json`**（6.7 MB）
  Model 2 `100_customers/C101` 的合并 E2 路线列池，16,336 条路线，由多次 per-satellite
  branch-cut-and-price 累积得到。受限列池主问题在该池上 Gurobi OPTIMAL = 1829.0094。
  保留此列池可直接复现该结果，无需重跑全部 BCP（重新生成约数小时）。

## 复现指引

```bash
# 精确 vs ALNS（小规模契约回归）
PYTHONPYCACHEPREFIX=/tmp/2e_vrptw_pycache \
  .venv/bin/python experiments/test_exact_contract.py

# M1/M2 精确 vs ALNS（各规模）
.venv/bin/python experiments/run_model12_exact_vs_alns_m5.py \
  --scales 50_customers --instances C101 --models 2 \
  --exact-time-limit 7200 --skip-alns \
  --warm-start-exact --gurobi-preset bound --save-gurobi-log
```

复现 1829 路线池主问题需要本目录的列池与研究 worktree 的
`models/model_2/route_pool_exact.py` + `experiments/run_m2_route_pool_master_probe.py`
（接受 `--columns-json`）。
