# 2E-VRPTW-PDD 项目总览

两级带时间窗取送货车辆路径问题（Two-Echelon Vehicle Routing Problem with Time Windows
and Pickup & Delivery）的求解框架。两级（E1 depot↔前置仓 satellite、E2 前置仓↔客户）、
带时间窗、带取送货（配送 + 容器回收）、可选虚拟中转（transshipment）、
可选顾客-前置仓自由分配（Model 6/7）。

本文档是整个项目的统一总览：模型体系、每个模型有哪些求解方法、精确求解的实现与
已证明结果、启发式求解、数据与实验、当前状态与已知限制。

**项目研究定位（2026-08-17）**：小规模用精确求解证明模型与实现正确性，大规模用 ALNS
证明求解能力和管理价值。推荐主线是 M1/M2 exact baseline + M6/M7 机制消融 +
M5/M6 大规模 ALNS。M6/M7 的路线列结果是 restricted-pool incumbent，不作为精确基准。
实验主线详见 [`experiment_roadmap.md`](experiment_roadmap.md)。

---

## 1. 模型体系（Model 1–7）

七个模型分为两组：**Model 1–5** 是递进叠加关系（取送货 + 中转），
**Model 6–7** 是自由分配变体（顾客归属前置仓为决策变量）。所有模型都是两级。

### 1.1 Model 1–5：取送货 + 中转递进

| 模型 | 语义 | 取送货(PD) | 中转(transshipment) |
|---|---|:---:|:---:|
| **Model 1** | 纯配送 baseline | ✗（仅配送 Sxd / Cxd） | ✗ |
| **Model 2** | 取送货 PDP | ✓（加 Sxp / Cxp 容器回收，LIFO 先送后取） | ✗ |
| **Model 3** | 取送货 + 联合中转 | ✓ | ✓（路线与中转在**一个** MIP 里联合优化） |
| **Model 4** | 取送货 + 两阶段中转 | ✓ | ✓（阶段一先定 M2 路线，阶段二固定路线后做中转选择 MIP） |
| **Model 5** | 取送货 + 每轮中转试探 | ✓ | ✓（ALNS 每个 candidate 内嵌受控中转试探，portfolio V1–V6） |

- **取送货**：E1 把配送送到前置仓（Sxd），再把前置仓攒的容器/回收物拉回 depot（Sxp）；
  E2 给客户配送（Cxd），再回收容器（Cxp）。约束：同一客户的 pickup 必须在 delivery 之后。
- **中转（relocation）**：把某个 E2 pickup 任务从原客户位置"搬"到同卫星的另一个客户位置，
  生成一对 transport pickup/drop 由另一辆 E2 车服务，可能省距离。M3/M4/M5 三种不同触发
  与优化方式，中转定义相同。
- **Model 1** 是退化基线（无取送货、无中转），用于和完整模型对比、做精确求解基准。


### 1.2 Model 6–7：自由分配变体

Model 6 和 Model 7 把**顾客-前置仓分配内生化**——顾客归属哪个前置仓不再是数据固定的
`assigned_sat`，而是二元决策变量 `w[c,s]`。其余机制与对应基线模型一致。

| 模型 | 基线 | 取送货(PD) | 自由分配 | 语义 |
|---|---|:---:|:---:|---|
| **Model 6** | M2 | ✓ | ✓ | M2 + 顾客-前置仓分配内生化（w[c,s] 决策变量） |
| **Model 7** | M1 | ✓ | ✓ | M1 + 顾客-前置仓分配内生化（纯配送 + 自由分配） |

- **自由分配**：`w[c,s]=1` 表示顾客 c 归属前置仓 s。E2 车队按前置仓独立，
  E1 路线由分配决定（无顾客的前置仓自动关闭）。
- **M6** = M2 + 自由分配：解空间是 M2 的严格超集，50c 优化 2.1%。
- **M7** = M1 + 自由分配：纯配送版本，E2 任务数减半，模型更紧凑。
- 详见 [`docs/model6_free_assignment.md`](model6_free_assignment.md) 和
  [`docs/model7_free_assignment_pure_delivery.md`](model7_free_assignment_pure_delivery.md)。

---

## 2. 求解方法总览

每个模型可有一种或多种求解方法：**精确（Gurobi MIP）** 与 **启发式（ALNS）**。
Model 2 另有**路线 master / pricing / universe** 一组 branch-price 基础组件。

| 模型 | 弧流精确 MIP | 路线 master/pricing（branch-price 基础） | ALNS 启发式 |
|---|:---:|:---:|:---:|
| Model 1 | ✅ `solve_exact` | — | ✅ `solve_alns` |
| Model 2 | ✅ `solve_exact` | ✅ route_universe / route_master / route_pricing | ✅ `solve_alns` |
| Model 3 | ⚠️ `solve_exact`（仅小规模） | — | ✅ `solve_alns` |
| Model 4 | ⚠️ `solve_exact`（两阶段，非全局） | — | ✅ `solve_alns` |
| Model 5 | ❌ 无 | — | ✅ `solve_alns` |

**精确求解的核心契约**：只有 Gurobi 在**未缩减候选空间**的模型上返回 `OPTIMAL`，才记为
`proven_optimal`（精确解）。限时 incumbent 标 `feasible_not_proven`，受限列池/单分支节点
标 `restricted_*` / `branch_node_optimal`，均不计入 Exact 均值或成功率。详见
[`docs/exact_solver_architecture.md`](exact_solver_architecture.md)。

---

## 3. 精确求解：实现状况与已证明结果

### 3.1 各模型精确求解能力

**Model 1（纯配送，弧流 MIP）— 全规模可证明最优**
- `solve_exact`：E1/E2 弧流 + 时间窗 + 载重/体积。注意：当前 M1 建模是**旧式固定
  M=100000 大 M**（无弧特定 Big-M、无不可行弧固定、无初始载量精确等式、无对称破除），
  比 M2 的 curation 弱，LP 界也更松。
- 已证明（C101）：5c=369.15、10c=474.86、50c=1107.75（Gurobi 直接 OPTIMAL，59s，可复现）。
  **100c 的 1622.92 不可复现**：2026-08-10 直接求解 1800s 找到更优可行解 1602.22、
  界 1166.95、gap 27.2%，未证明；旧数值来源待查证。
- 状态：⚠️ ≤50c 直接可证明；100c 直接求解 30 分钟未闭合。

**Model 2（取送货，弧流 MIP）— 小规模直接可证明，中/大规模受限**
- `solve_exact`：E1/E2 联合弧流 + 取送货 LIFO + TFp 取送时间衔接 + E1↔E2 时间耦合。
  curation 同样做了弧 Big-M / 不可行弧 / 初始载重等式 / 同客户 p→d 禁止等加强。
- 已证明（C101）：5c=391.57、10c=543.09。50c=1289.06 曾报告 proven_optimal
  （下界注入 + warm start 后闭合），**2026-08-10 复现 600s 仅到 gap 0.18%，需复核**。
- **直接求解实测（2026-08-10）**：50c 裸跑 900s 无可行解、界 1232.68；
  带下界+warm start 600s 到界 1289.056（gap 0.18%）；100c 600s 无可行解、界 1657.70。
  因此 **100c 的 1829.0094 不再按直接求解口径计为 proven_optimal**——它来自证书研究路径
  （E1 阶跃函数 + S1 floor 精确定价证书），作为研究性下界证据保留。
- 路线 master / pricing / universe（branch-price 基础，**均不单独认证**）：
  - `route_universe`：穷举 E2 路线宇宙（≤10 任务/卫星、≤250k 路线），完备+OPTIMAL 才认证。
  - `route_master`：车/时间感知直接主问题，支持 Ryan-Foster same/different 分支；仅完整宇宙时认证。
  - `route_pricing`：单条 E2 路线精确定价 oracle。
  - 完整 branch-price-and-cut 闭环（主问题对偶→定价→列加入→分支树→割）**尚未实现**。
- 状态：⚠️ ≤10c 直接可证明；50c 直接求解复现中（gap 0.18%）；**100c 直接求解不可闭合**。
  100c 路线宇宙实测约 1190 万条（S1 单卫星 423 万），全宇宙直接 MIP 不现实。

**Model 3（取送货 + 联合中转 MIP）— 不 scale**
- `solve_exact`（`exact_joint.build_model3_joint_exact`）：路线 + 中转重定位在**一个** MIP
  联合优化。`max_relocation_candidates=None`（全候选）才允许认证。
- 状态：⚠️ 仅小规模能跑（5/10c 常 TIMEOUT，返回 feasible incumbent）；**≥50c 模型构造本身
  爆炸/挂死**（全中转候选枚举），受限候选则产出退化空模型。run_comparison 在 ≥50c 跳过
  M3 exact（`UNAVAILABLE_M3_NOT_SCALABLE`）。**M3 目前只能靠 ALNS。**

**Model 4（两阶段中转）— 非全局精确**
- `solve_exact`：阶段一跑 M2 弧流定路线，阶段二在固定路线上用 0-1 MIP 精确选中转方案
  （`transshipment_mip`，set-packing）。阶段二虽精确，但整体**不是**全局精确算法。
- 状态：始终标 `two_stage_not_global`，`proven_optimal` 恒为 False。run_comparison 跳过 M4 exact。

**Model 5 — 无精确求解**
- 只有 ALNS。`main.py` 对 M5 exact 显式报错。

**Model 6（自由分配取送货，受限路线列主问题）——50/100c 非全局证明**
- `models/model_6/route_pool.py`：路线列重构 + `w[c,s]` 自由分配。
  用路线变量 `y[rid]` 替代 ~285k 个 E2 弧变量，singleton 列 + ALNS 提取列。
- 受限列池结果（C101）：50c=1285.14（4.5s）、100c=1867.95（101.5s）；Gurobi `OPTIMAL` 仅表示列池内最优。
- 详见 [`docs/exact_50c_results.md`](exact_50c_results.md) 与 [`column_generation_m6_m7_diagnostic.md`](column_generation_m6_m7_diagnostic.md)。

**Model 7（自由分配纯配送，受限路线列主问题）——非全局证明**
- `models/model_7/route_pool.py`：M6 的纯配送版本，E2 任务数减半（无回收）。
- 受限列池结果（C101）：10c=474.86（0.0s）、50c=1127.52/后续找到 1118.55、100c=1566.98（220.6s）；
  均不是全局精确证明。
- 详见 [`docs/model7_free_assignment_pure_delivery.md`](model7_free_assignment_pure_delivery.md)。

### 3.2 精确求解的统一返回契约

所有精确入口支持 `return_info=True` 返回 `(e1_routes, e2_routes, info)`。`info` 关键字段：
`status`（Gurobi 原始状态）、`objective`/`best_bound`/`mip_gap`、`solution_type`、
**`proven_optimal`**（唯一决定是否计为精确解）。

### 3.3 精确求解证据

固化在 [`reference/exact_proof_evidence/`](../reference/exact_proof_evidence/)：
8 卫星 BCP 最优值、M2 50c warm-start 路线、S5 branch-price 闭合证明、16,336 列合并池。
完整研究日志在研究分支 `experiments/exact_worktree_progress.md`。

---

## 4. 启发式求解（ALNS）

所有 5 个模型都有 ALNS 启发式（`models/model_N/alns_solver.py`），共享引擎
`models/alns_engine.py`（Ropke-Pisinger 风格：轮盘算子选择、模拟退火接受、σ1/2/3 评分、
权重衰减）与数据结构 `models/alns_base.py`（Route/Solution，含虚拟中转节点表示）。

- **M1/M2**：纯 destroy/repair ALNS（random/worst/shaw removal + greedy/regret insertion）。
- **M3**：ALNS 内嵌中转算子 + 周期性 `optimize_transshipment`。
- **M4**：干净两阶段——阶段一普通 ALNS（无中转），阶段二 `postprocess_transshipment`。
- **M5**：每个 candidate 内嵌受控中转试探 + strong initial 强可行初始解 + 38 个 portfolio
  分支（V1→V6 演进）。当前推荐 **Model 5-Hybrid-Safety-Optimized-V6**。

**Model 5 V6 对比结果**（120 个非北京算例，统一强初始解）：
vs 强 Model 2 做到 **92 更好 / 26 持平 / 0 更差，平均成本 −3.91%**。详见
[`docs/model5_optimization_summary.md`](model5_optimization_summary.md) 与 README。

注意：M5 portfolio 跑 38 个分支取最优，单算例耗时远高于单次 M2 ALNS，故对比的是**解质量**
而非等壁钟公平。M3/M5 共用 M3 的算子机制（M5 不是 M3 的严格超集，删 M3 会坏 M5）。

---

## 5. 数据与对比框架

**数据**（`data/`）：Solomon 风格非北京算例 5/10/15/20/50/100 客户各 30 个 + `beijing_fresh`
北京生鲜算例。15/20 客户为后加的中间规模（当前无对比脚本引用）。

**两条对比流水线**（互相隔离）：
1. `run_comparison.py`：Model 1–4 × {exact, alns} × 4 规模。exact 行按 `proven_optimal`
   门控；M4 exact、M3 exact(≥50c) 跳过；50/100c 的 exact 带 ALNS warm-start。
2. `experiments/run_model2345_all_nonbeijing.py`：Model 2/3/4/5 的 ALNS 公平对比（120 算例），
   README 头条 M5 结果来自这里。

`experiments/run_model12_exact_vs_alns_m5.py`：M1/M2 精确 vs ALNS vs M5 的诊断驱动。
`main.py`：单算例配置驱动入口。

---

## 6. 当前状态与已知限制（截至 2026-08-16）

**能用的**：
- M1 精确：≤50c 可证明最优（Gurobi 直接 OPTIMAL）；100c 未闭合。
- M2 精确：≤10c Gurobi 直接 OPTIMAL；50c 路线列主问题 + BCP 下界 46.4s OPTIMAL；
  100c E1 前沿枚举 + floor 证书分解证明最优（1829.0094）。
- **M6 restricted route-pool：50c=1285.14、100c=1867.95**（列池内最优，非全局证明；
  2026-08-17 修正口径，见 `column_generation_m6_m7_diagnostic.md`）。
- **M7 restricted route-pool：10c=474.86、50c=1118.55（CG 找到更好列后）、100c=1566.98**
  （列池内最优，非全局证明）。
- M2–M5 ALNS：全部稳定可用，M5 V6 是推荐版本。
- 精确求解契约 + 回归测试 `experiments/test_exact_contract.py` 全绿。
- 联合 branch-price 根诊断工具：`experiments/run_joint_branch_price_root.py`（LP 天花板探测）。

**已知限制 / 待办**：
- **M3 精确不 scale**（≥50c 构造爆炸）；M3 只能 ALNS。
- **M2 弧流 LP 下界弱**：100c LP 天花板 ~1567.47（E1 LP integrality gap）；
  收紧需要 E1 整数约束或割平面。
- **测试薄弱**：仅 `test_exact_contract.py` + `test_e2_start_time_regression.py` 有真断言；
  无 pytest/CI；大 ALNS solver 无自动化测试。
- **完整 branch-price-and-cut 树未实现**：根节点 LP 不能自证整数最优，
  需要分支与割平面；严格闭合定理只覆盖个别实例且不计入直接求解口径。

**最近工作**（2026-08-11 → 2026-08-16）：
- **M6 受限路线列池求解**：50c/100c C101 均达列池内 Gurobi OPTIMAL（`models/model_6/route_pool.py`），后经口径修正不作为全局精确证明。
- **M7 新模型**：M1 + M6 自由分配 = 纯配送自由分配模型（`models/model_7/route_pool.py`）。
  10c/50c/100c C101 全部为受限列池 OPTIMAL，不作为全局精确证明。
- **M2 100c 分解证明完成**：E1 前沿枚举（18 plans, 3 Pareto classes）+ floor 证书，证明最优值 1829.0094。
- 精确求解结果文档更新：[`docs/exact_50c_results.md`](exact_50c_results.md)。

**早期工作**（2026-08-03 → 2026-08-10）：
- 从 worktree 移植联合 branch-price 根基础设施：`route_pool_exact.py`、`e2_route_cg.py`、
  `joint_pricing.py`、`joint_branch_price_root.py`、`lagrangian_decomp.py`
- 从 worktree 移植严格闭合定理证明：`probe_e1_stepfn.py`（E1 阶跃函数）、
  `probe_s1_floor_cert.py`（S1 floor 精确定价证书）
- **口径收紧（2026-08-10）**：proven_optimal 只来自 Gurobi 对完整模型的直接 OPTIMAL；
  100c C101 的证书路径结论改记为研究性证据（1829.0094 非直接求解认证）。
  直接求解实测：100c 600s 无可行解/界 1657.70；50c 带辅助 600s gap 0.18%。

---

## 7. 代码结构

```text
core/          实例、节点、车队数据结构（ProblemInstance / Task / Fleet）
models/        Model 1-7、ALNS 公共组件（alns_base/alns_engine/strong_initial_solution/solver_utils）
  model_2/     还有 route_master / route_universe / route_pricing（branch-price 基础）
  model_3/     exact_joint.py（联合中转 MIP）
  model_4/     transshipment_mip.py（阶段二中转选择 MIP）
  model_6/     route_pool.py（自由分配取送货路线列主问题）
  model_7/     route_pool.py（自由分配纯配送路线列主问题）
experiments/   批量实验与对比脚本、test_exact_contract.py 回归测试
scripts/       数据集生成、论文图生成
data/          Solomon 风格算例 + 北京生鲜算例
config/        app/fleet/economic_cost 配置
reference/     exact_proof_evidence/（精确求解证据）+ 文献/参数追溯
docs/          本文档、exact_solver_architecture、model5_optimization_summary、
               model6_free_assignment、model7_free_assignment_pure_delivery、exact_50c_results
output/        实验输出（gitignore，磁盘留存）
```

## 8. 常用命令

```bash
# 精确求解契约回归（必跑）
PYTHONPYCACHEPREFIX=/tmp/2e_vrptw_pycache .venv/bin/python experiments/test_exact_contract.py

# 单算例
python main.py

# 全部非北京算例 ALNS 对比（M2-5）
python experiments/run_model2345_all_nonbeijing.py --iterations 80 --time 10

# M1/M2 精确 vs ALNS 诊断
python experiments/run_model12_exact_vs_alns_m5.py --scales 50_customers --instances C101 --models 2

# M2/M6/M7 路线列实验（M6/M7 为受限列池，非全局证明）
PYTHONPYCACHEPREFIX=/tmp/pycache .venv/bin/python \
    experiments/run_50c_exact_m2_m6.py --scale 100_customers --skip-m2 --m6-time 600
```
