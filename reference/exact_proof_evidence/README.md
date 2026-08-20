# 精确求解证明证据（curated）

本目录固化 2E-VRPTW-PDD 精确求解研究的**不可替代证据**：直接求解结果的关键 JSON
与复现 1829 路线池主问题所需的合并列池。完整的实验代码、Gurobi 日志和 per-run
列转储见研究 worktree（分支 `worktree-lagrangian-m2`）。

**口径（2026-08-10）**：`proven_optimal` 只授予 Gurobi 对完整未缩减模型直接求解的
`OPTIMAL`。下方标"证书/研究证据"的行**不计入**已证明结果。

## 结果总览

| 算例 | 结果 | 状态 |
| --- | --- | --- |
| Model 1 `100_customers/C101` | 1622.92 | **不可复现**：2026-08-10 直接求解 1800s 找到更优可行解 1602.22、gap 27.2%，未证明 |
| Model 2 `10_customers/C101` | 543.09 | Gurobi OPTIMAL |
| Model 2 `50_customers/C101` | 1289.06 | 曾报告 Gurobi OPTIMAL（下界注入 + warm start 后 20.9s 闭合）；2026-08-10 复现 600s 仅到 gap 0.18%，需复核 |
| Model 2 `100_customers/C101` 全部 8 个 E2 卫星 | 见下 | 研究证据：各卫星独立 BCP，均 OPTIMAL，和 1031.8799 |
| Model 2 `100_customers/C101` 受限列池主问题 | 1829.0094 | 研究证据：Gurobi OPTIMAL（仅对该 16,336 列池，**非**全路线宇宙） |
| **Model 2 `100_customers/C101` 全模型** | **1829.0094** | **研究证据（非直接求解认证）：严格闭合定理（2026-08-03）；直接求解 600s 无可行解、界 1657.70** |

## 100c C101 的证书研究路径：严格闭合定理

**注意**：以下结论不满足“Gurobi 直接 OPTIMAL”口径，仅作研究性证据。

证明通过 `experiments/probe_e1_stepfn.py` + `experiments/probe_s1_floor_cert.py` 完成。

**定理**：将可行域按 S1 发车底线 tau 划分为两个区域：
- **tau < tau_min = 153.0776**：E1_coupled(tau) = 797.1295（无节省）→ total ≥ 1829.0094
- **tau ≥ tau_min**：E2_S1(s_max ≥ 153.08) ≥ 213.0745（全路线宇宙认证）→ total ≥ 1839.08 > 1829.0094
→ 对所有可行 tau，total ≥ 1829.0094 → 1829.0094 是全局最优。

## 复现命令

```bash
# 精确求解契约回归
PYTHONPYCACHEPREFIX=/tmp/2e_vrptw_pycache .venv/bin/python experiments/test_exact_contract.py

# 严格闭合定理 Step 6（E1 阶跃函数，~2 分钟）
.venv/bin/python experiments/probe_e1_stepfn.py

# 严格闭合定理 Step 5（S1 floor 精确定价证书，~15 秒）
.venv/bin/python experiments/probe_s1_floor_cert.py --pricing-method mip

# 联合 BCP 根诊断（LP 天花板探测，~12 秒）
.venv/bin/python experiments/run_joint_branch_price_root.py \
    --scale 100_customers --instance C101 \
    --columns-json reference/exact_proof_evidence/merged_columns/100_customers__C101__columns.json \
    --satellite-lb-json reference/exact_proof_evidence/exact_incumbents/m2_100_c101_satellite_bcp_exact_lbs.json \
    --lp-ceiling-only
```

## 文件说明

### `exact_incumbents/`
- `m2_100_c101_satellite_bcp_exact_lbs.json` — 8 卫星 BCP 最优值（和 1031.8799）
- `m2_50_c101_1291.routes.json` — 50c C101 warm-start 路线解
- 其他文件 — S5 branch-price 闭合证明的中间产物

### `merged_columns/`
- `100_customers__C101__columns.json`（6.7 MB, 16,336 列）— 合并 E2 路线列池
