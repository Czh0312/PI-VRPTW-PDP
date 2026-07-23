# 2E-VRPTW-PDD 项目总览

两级带时间窗取送货车辆路径问题（Two-Echelon Vehicle Routing Problem with Time Windows
and Pickup & Delivery）的求解框架。两级（E1 depot↔前置仓 satellite、E2 前置仓↔客户）、
带时间窗、带取送货（配送 + 容器回收）、可选虚拟中转（transshipment）。

本文档是整个项目的统一总览：模型体系、每个模型有哪些求解方法、精确求解的实现与
已证明结果、启发式求解、数据与实验、当前状态与已知限制。

---

## 1. 模型体系（Model 1–5）

五个模型是**递进叠加**关系，每一级在前一级基础上加一类机制。所有模型都是两级。

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
- `solve_exact`：E1/E2 弧流 + 时间窗 + 载重/体积。curation 加强：弧特定时间 Big-M、
  不可行弧固定、初始载重等式、车辆对称破除。
- 已证明（C101）：5c=369.15、10c=474.86、50c=1107.75、**100c=1622.92（proven_optimal，~1800s）**。
- 状态：✅ 全规模精确可用。

**Model 2（取送货，弧流 MIP）— 小/中规模可证明，大规模受限**
- `solve_exact`：E1/E2 联合弧流 + 取送货 LIFO + TFp 取送时间衔接 + E1↔E2 时间耦合。
  curation 同样做了弧 Big-M / 不可行弧 / 初始载重等式 / 同客户 p→d 禁止等加强。
- 已证明（C101）：5c=391.57、10c=543.09、**50c=1289.06**（proven_optimal；50c 需要
  先用 S5 branch-price 求出下界、注入弧流后 20.9s 闭合）。
- **100c = 1829.0094（未由 Gurobi 证明全局最优）**：8 个 E2 卫星已独立 branch-cut-and-price
  证明最优（和 1031.8799 = incumbent 的 E2 代价）；受限列池（16,336 列）主问题 OPTIMAL=
  1829.0094；全模型残余 gap 0.2615%（bound 1824.2267），全部来自 E1↔E2 配送耦合。
  Lagrangian 分解攻击该 gap 已确认是死路；另有手写严格闭合定理声称全局最优（非 Gurobi 证书）。
- 路线 master / pricing / universe（branch-price 基础，**均不单独认证**）：
  - `route_universe`：穷举 E2 路线宇宙（≤10 任务/卫星、≤250k 路线），完备+OPTIMAL 才认证。
  - `route_master`：车/时间感知直接主问题，支持 Ryan-Foster same/different 分支；仅完整宇宙时认证。
  - `route_pricing`：单条 E2 路线精确定价 oracle。
  - 完整 branch-price-and-cut 闭环（主问题对偶→定价→列加入→分支树→割）**尚未实现**。
- 状态：✅ ≤50c 精确可用；⚠️ 100c 只有 incumbent（受限列池 OPTIMAL，全模型未证明）。

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

## 6. 当前状态与已知限制（截至 2026-07-23）

**能用的**：
- M1 精确：全规模可证明最优。
- M2 精确：≤50c 可证明最优；100c 有强 incumbent + 受限列池 OPTIMAL。
- M2–M5 ALNS：全部稳定可用，M5 V6 是推荐版本。
- 精确求解契约 + 回归测试 `experiments/test_exact_contract.py` 全绿。

**已知限制 / 待办**：
- **M2 100c 全局最优未证明**（0.2615% E1↔E2 耦合 gap）；完整 branch-price-and-cut 未实现。
- **M3 精确不 scale**（≥50c 构造爆炸）；M3 只能 ALNS。
- **M2 弧流 LP 下界弱**：100c 即便 warm-start，gap 仍 ~47%（下界 910 vs incumbent 1718）；
  收紧需要接 E2/E1 cost lower-bound cuts（hooks 已在，未接）。
- **测试薄弱**：仅 `test_exact_contract.py` + `test_e2_start_time_regression.py` 有真断言；
  无 pytest/CI；大 ALNS solver 无自动化测试。
- **README"当前重点"**仍写 M5 V6，未报告精确求解突破（本文档 + README 精确结果章节已补）。

**最近工作**（部分在 main 已提交，部分在工作区未提交）：
- main 已提交：精确求解 curation、warm-start 接线（M1/M2/M3）、M3 大规模保护、README 精确结果章节、
  证据固化。
- **工作区未提交**：M2 warm-start 修复——加强版 M2 之前拒绝 ALNS warm-start MIP start
  （50/100c 返回 NO_PROOF）；修复后写完整连续变量 + 过滤禁用弧，M2 50c 从无解 →
  feasible_not_proven 1326.34（Gurobi 接受 start，SolCount 10）。

---

## 7. 代码结构

```text
core/          实例、节点、车队数据结构（ProblemInstance / Task / Fleet）
models/        Model 1-5、ALNS 公共组件（alns_base/alns_engine/strong_initial_solution/solver_utils）
  model_2/     还有 route_master / route_universe / route_pricing（branch-price 基础）
  model_3/     exact_joint.py（联合中转 MIP）
  model_4/     transshipment_mip.py（阶段二中转选择 MIP）
experiments/   批量实验与对比脚本、test_exact_contract.py 回归测试
scripts/       数据集生成、论文图生成
data/          Solomon 风格算例 + 北京生鲜算例
config/        app/fleet/economic_cost 配置
reference/     exact_proof_evidence/（精确求解证据）+ 文献/参数追溯
docs/          本文档、exact_solver_architecture、model5_optimization_summary
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
```
