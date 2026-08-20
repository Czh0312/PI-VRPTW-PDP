# Model 2 联合 Branch-Price 根 — 实现设计（Path B）

> 状态（2026-08-10）：本设计未落地为完整 BCP 根；`100_customers/C101` 的闭合结论
> 来自证书路径（E1 阶跃 + S1 floor 证书），不满足“Gurobi 直接求解 OPTIMAL”口径。

目标：实现缺失的联合 branch-price 根，闭合 `100_customers/C101` 的 0.2615% gap
（incumbent **1829.0094** vs bound **1824.2267**），使 Model 2 在 100 客户达到
`proven_optimal`。工作目录：本 worktree（`route_pool_exact.py` / `e2_route_cg.py` /
`e2_ng_dssr_pricing.py` 在此）。

## 可解性判定（已由设计 workflow 确认）

**可行、不大**。三个关键事实：

1. 联合主问题 `route_pool_exact.py` 的 E1↔E2 耦合**只有两个 linking 族**：
   `Link_Start_{sat}_{rid}_{k}`（S[rid] ≥ E1 配送到达）和 `Link_Pickup_{sat}_{rid}_{k}`
   （E1 取货到达 ≥ R[rid]）。`A2[task]` **不出现在任何 E1 耦合行**。所以每条路线的
   耦合面只有两个标量：发车 `S` 和返回 `R`。
2. 固定路线下，对 linking 对偶 σ_s、ρ_s 的最优 schedule 是**闭式零等待前向传播**
   （所有约束 ≥，目标系数非正 → 每个变量压到下界），无需解 LP。
3. 联合 reduced cost = 现有 per-satellite reduced cost + 可加的 `ρ_s·Δduration` 弧惩罚
   + 一个单调的 `Ŝ` 资源。现有 `_solve_pricing_mip`（`e2_route_cg.py:362-790`）"加两个
   连续变量 + 目标项"就是联合定价 MIP。

## #1 风险（必须验证）

**Big-M LP 退化**：`Link_Start/Link_Pickup` 用 `M=100000` 和 `y·visits` 乘积，LP 松弛下
`visits_del` 分数 → σ/ρ 对偶数值失真 → bound 不升或给出**假证书**。
缓解（设计 §5）：用凸组合加权聚合
```
sigma_start[s] = sum_{rid,k} pi(Link_Start[s,rid,k]) * visits_del[k].X  / max(n_selected_routes, 1)
rho_pickup[s]  = sum_{rid,k} pi(Link_Pickup[s,rid,k]) * visits_pick[k].X / max(n_selected_routes, 1)
```
若仍 stall → 升级到 disaggregated-λ 重构（`_add_linking_constraints` 加 `λ[rid,k]` 赋值
变量，`Σ_k λ = y[rid]`，把 `M*(2-y-visits)` 换成 `M*(1-λ)`）。

## 实现计划（文件级，~640 LOC）

| 文件 | 动作 | LOC | 说明 |
|---|---|---|---|
| `models/model_2/route_pool_exact.py` | 改 | ~70 | 实现 `return_duals`（现在 line 56 是死的）+ 加 `extract_joint_duals(m, vars_dict, ctx, e2_tasks_by_sat)`：读 `E2_Cover_{t}.Pi`、`E2_RouteLimit_{s}.Pi`、`Link_Start_{s}_{rid}_{k}.Pi`、`Link_Pickup_{s}_{rid}_{k}.Pi`、`E2_TaskTWlo/hi_{t}.Pi`、（可选）`E2_SatCostLB_{s}.Pi`，按上面公式聚合成 per-sat σ_s/ρ_s |
| `models/model_2/joint_pricing.py`（新）| 新 | ~250 | `solve_joint_pricing_mip(problem, sat_id, tasks, pi_cover, nu_vehicle, sigma_start, rho_pickup, alpha_lo, alpha_hi, same_pairs, diff_pairs, pricing_time_limit, proof_as_feasibility, forbidden_paths, cut_duals)`：复制 `_solve_pricing_mip` 全部脚手架（y/z/A/Q/Vol/order/MTZ/容量/precedence/SRI/Ryan-Foster），加连续 `S_var`（lb=0）、`R_var`，加约束 `S_var ≤ A[first_task]`、`R_var ≥ A[last]+svc+travel_back`，目标改 `route_cost − Σπ·y − ν − Σcut·h − σ·S_var − ρ·R_var + Σ(α_lo−α_hi)·A`；proof 模式同理 |
| `models/model_2/joint_branch_price_root.py`（新）| 新 | ~200 | `solve_joint_branch_price_root(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite, initial_columns, max_cg_iterations=200, pricing_time_limit=20, proof_as_feasibility=True)`：CG 循环 = 联合 LP 主问题（`build_route_pool_master(relax=True, return_duals=True)`，barrier 法）→ `extract_joint_duals` → 每卫星 `solve_joint_pricing_mip` → 去重加列 → 直到无负 reduced cost 列（`LP_PROVED`）。返回 lp_bound/incumbent/gap/proven_optimal |
| `experiments/run_joint_branch_price_root.py`（新）| 新 | ~120 | CLI，镜像 `run_m2_route_pool_master_probe.py`。`--columns_json`（默认 16,336 列合并池，路径见 `analyze_m2_100_gap_certificate.py:78-79`）、`--max_cg_iterations`、`--pricing_time_limit`、`--proof_as_feasibility`、`--output_dir` |

复用不改：`e2_route_cg._solve_pricing_mip`（模板）、`_solve_pricing_labeling_exact`
（可选加速）、`route_pool_exact._normalize_columns / _route_cost / _phys / _service_time`、
`E2RouteColumn`。**E1 侧保持弧流**（不做 E1 列生成；E1 对偶只通过 linking 进 E2 定价）。

## 联合定价 reduced cost（精确）

候选路线 r（卫星 s，任务 T，schedule `S, A2_*, R`）：
```
RC(r, schedule) = Σ_arcs dist
                − Σ_{t∈T} π_t                # E2_Cover
                − ν_s                         # E2_RouteLimit (≤0)
                − σ_s · S                      # Link_Start（σ_s≥0 → 想早发车）
                − ρ_s · R                      # Link_Pickup（ρ_s≥0 → 想早返回）
                + Σ_t (α_lo_t − α_hi_t)·A2_t   # E2_TaskTW，通常≈0
                − [ satcostlb·cost(r) 若激活 ]
```
最优 schedule = 零等待前向传播，闭式。

## 验证门（防假证书，严格按序）

1. **15 客户 smoke**（`data/15_customers/C101.csv`，亚秒 LP）：联合 LP bound 必须**高于**
   per-satellite 和（说明 linking 对偶被正确激活）。若没升高 → `extract_joint_duals`
   聚合错，停下调试，**绝不继续**。
2. 通过后 **100 客户 C101**：bound 从 1824.2267 升向 1829.0094。
   - 到 ≥1829.0094−1e-3 → **根节点 proven_optimal**（目标）。
   - 1828.0–1829.0 → 真进展，加 SRI 割继续（镜像 `e2_route_cg:338-352` + `run_satellite_branch_price_probe:535-590`）。
   - ≤1825 → 联合定价没激活 linking，查对偶聚合。
3. 整数主问题用现有 incumbent 路线 warm-start（`_apply_mip_start` 已在 `route_pool_exact.py:203-245`）。

## 回退（若联合定价真 stall）

per-satellite BCP bound（1031.8799，已证）+ Lagrangian E1 耦合上界检查
（`experiments/run_m2_lagrangian_bundle.py` 已存在）——另一条不需联合定价的证书路径。

## 工程注意

- 跑 Gurobi（M3/M2 大模型）一律用 **shell 内 `sleep+kill` 看门狗 + 直写日志、不用管道**，
  工具超时对后台大任务不生效（本会话有过 M3 8 小时挂死、`grep|tail` 吞缓冲的教训）。
- 这是**证明关键代码**：`proven_optimal` 契约不可破坏。任何 bound 异常就停，宁可报
  "未证"也不给假证书。
- 种子列池：`output/m2_full_bcp_100_c101_merged_columns/100_customers__C101__columns.json`（16336 列）。
