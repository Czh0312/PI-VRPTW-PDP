# Model 4：顾客-前置仓分配内生化的 2E-VRPTW-PDD

## 一句话定位

Model 4 与 Model 3 的唯一区别：**顾客归属哪个前置仓是决策变量**（z_cs），
而不是数据里固定输入的 `assigned_sat`。其余机制全部保持一致：

- 同一顾客的配送/回收任务归属同一前置仓（单分配，不做送收分离）；
- E2 车队按前置仓独立（`vehicles_per_satellite`），路线锚定本仓；
- E1 车队 8 辆、容量/体积/时间窗衔接规则与 M3 相同；
- 先送后取（同一顾客 pickup 晚于 delivery）、E1 回收晚于 E2 返回。

因此 M4 的解空间是 M3 的严格超集：M3 的任何固定分配可行解在 M4 中都是可行解。
任何成本差都归因于"自由分配"这一个自由度，实验对比干净。

## 建模要点

### E1 需求随分配线性变化

前置仓 s 的 E1 配送量 / 回收量 / 体积 / 截止时间由分配变量 w[c, s] 决定：

```text
配送重量(s) = Σ_c (demand_c + cw) · w[c, s]
回收重量(s) = Σ_c cw · w[c, s]
配送截止(s) = min_{c: w[c,s]=1} tw_del_c[1]
```

配送截止的 min 写成蕴含式：`w[c,s]=1 ⇒ E1 到达 S{s}d 不晚于 tw_del_c[1]`。

### E1 节点可选、载量线性化

- E1 配送/回收节点改为**可选访问**，并由 `served_s` 与分配链接：
  有顾客分配的前置仓必须被 E1 配送和回收各访问恰好一次。
- E1 初始载量用 y[c, s, k]（= w[c,s] ∧ E1 车 k 访问 S{s}d）线性化，
  避免 `重量表达式 × 访问二元变量` 的双线性项。

### E2 覆盖量与分配链接

每个前置仓以"全体顾客任务"为候选节点（不再按固定分配裁剪），并强制：

```text
Σ_{E2车 v} 访问(c的配送任务, 前置仓 s) == w[c, s]
Σ_{E2车 v} 访问(c的回收任务, 前置仓 s) == w[c, s]
```

## 实现

| 模块 | 内容 |
|---|---|
| `models/model_4/alns_solver.py` | 分配感知求值器（从解反推分配、动态重建 E1 任务）+ 跨仓 ALNS（顾客对级删除、多卫星插入） |
| `models/model_4/exact.py` | 小规模精确 MIP（z_cs + E1 线性化 + 覆盖链接），仅适合 5/10 客户 |
| `experiments/run_model4_vs_model3.py` | M4 vs M3 ALNS 对比脚本（统一 seed/预算，输出 md+csv） |

### ALNS 设计

- 分配由解隐式表达：顾客任务出现在哪个前置仓的 E2 路由桶，就归属哪个前置仓；
- E1 任务集（重量/体积/截止/回收）每次评估前按当前分配确定性重建；
- 破坏算子按**顾客**删除（d+p 成对）；
- 两个修复算子：① 同车成对联合插入（d+p 在同一辆 E2 车，逐仓比较最优位置）；
  ② 跨车顺序插入（先 d 后 p，p 可进入同仓另一辆车，保持单分配不变式）；
- E1 路线在配送节点集合不变时**保留结构**（E2 发车时刻稳定，减少候选被拒），
  仅当载量/时间在新分配下不可行时才重建；
- 每个候选后追加 **E1 2-opt 局部改进**（M3 靠 e1 echelon 做同样的事，M4 必须
  自己补上，否则 E1 路线从不被局部优化，搜索会明显变差）；
- 初始解从数据固定分配（及最近/次近前置仓种子）构造，再靠跨仓搬移改进。

### 回收时间的语义修正（2026-08-11）

原先 E1 回收任务的时间窗下界是静态大数 9999（ALNS 的 `tw[0]` 等待和 MIP 的
`E1_TW_lo` 都会强制回收发生在 ≥9999 时刻），真实的"回收 ≥ 该仓最后一个容器
返回时刻"耦合被这个占位符掩盖。已修正：

- E1 回收任务时间窗下界改为 0，真实下界由 **MIP `Link_Pickup` / ALNS 等待逻辑**
  动态施加：回收时刻 = max(E1 到达时刻, 该仓所有 E2 车辆返回时刻)；
- ALNS 允许 E1 回收车在前置仓等待（与 MIP 语义一致），不再硬性拒绝早到；
- `build_unified_pdp_model` 的时间上界改为由**实例的客户时间窗**推导（此前依赖
  9999 撑大 horizon；route-master 以空 E2 建基模型时，去掉 9999 会导致
  customer pickup ready 超出 task_time 上界而不可行）。

### 对比口径提醒

M4 每次迭代比 M3 重（要重建 E1、遍历全部前置仓），**迭代数对比会高估 M3**。
公平对比应使用纯时间预算（`--iterations` 设大、用 `--time` 限时）：
同 20s 预算下 50c RC201，M4 1494.94 vs M3 1495.27，基本打平。

### 精确 MIP 验证

`5_customers/C101` 与 `10_customers/C101`（单仓）的 M4 精确解分别为
**391.57** 与 **543.09**，与 M3 已证明最优一致。20 客户（4 仓）直接求解
模型过大（约 5 万二元变量、24 万约束），300s 内未闭合，说明 M4 精确
仅适合小规模验证；大规模依赖 ALNS。

## 已观察到的结果

30 算例（C101/C201/R101/R201/RC101/RC201 × 5 个规模）× 500 迭代 / 60s，
seed=42，成本 = E1+E2 距离（2026-08-11 最终版，含 E1 局部搜索与回收时间修正）：

| 规模 | M4更好 | 持平 | M4更差 | M3不可行但M4可行 | 平均gap% |
|---|---:|---:|---:|---:|---:|
| 5_customers | 2 | 4 | 0 | 0 | -1.39% |
| 10_customers | 6 | 0 | 0 | 0 | -7.15% |
| 20_customers | 5 | 0 | 0 | 1 | -6.24% |
| 50_customers | 5 | 0 | 0 | 1 | -9.10% |
| 100_customers | 5 | 0 | 0 | 1 | -9.51% |

要点：

- **30/30 算例 M4 不劣于 M3，0 更差**；
- 50c RC201 早期 M4 落后（+0.94%）的根因是 M4 缺少 E1 局部搜索；
  补上 E1 2-opt 后该算例 5/5 种子全胜、平均约 -3.5%；
- **恢复可行性**：20c RC101、50c RC101、100c R101 在固定分配下不可行，
  M4 均找到可行解（搬移 6/11/27 个顾客）——这是"分配自由度"最直观的价值；
- 100c 平均 -11.14%（搬移约 15 个顾客），说明规模越大自由分配的收益越明显；
- ⚠️ 10c 全为单前置仓算例，-7.15% 全部来自修复算子差异（成对联合插入优于
  M3 的单任务插入），与自由分配无关；多仓算例（20c+）的收益才是自由分配贡献。

## 运行

```bash
# 单算例（config/app_config.json 设 model=6）
PYTHONPYCACHEPREFIX=/tmp/pycache .venv/bin/python main.py

# 对比实验
PYTHONPYCACHEPREFIX=/tmp/pycache .venv/bin/python \
experiments/run_model4_vs_model3.py \
  --scales 20_customers 50_customers 100_customers \
  --iterations 99999 --time 15 --tag v1

# 小规模精确验证
PYTHONPYCACHEPREFIX=/tmp/pycache .venv/bin/python - <<'PY'
import json
from core.instance import ProblemInstance
from utils.data_loader import parse_dataset
from models.model_4.exact import solve_exact

fleet = json.load(open('config/fleet_config.json'))
nodes, _ = parse_dataset('data/5_customers/C101.csv')
problem = ProblemInstance(nodes, fleet, speed=1.0)
problem.aggregate_demands_from_pre_assignment()
e1, e2, assign, info = solve_exact(
    problem, problem.fleet.e2_per_sat_count, time_limit=120, return_info=True)
print(info['proven_optimal'], info['objective'])
PY
```

---

## 单前置仓完整程序 MIP（5c/10c）

当实例只有一个前置仓，且全部顾客的一次 E1 配送/回收分别不超过 E1 容量时，
自由分配变量退化为固定值。E1 的最优距离固定为
`depot -> S_d -> S_p -> depot`，剩余决策可以完整地写成一个覆盖全部顾客的
E2 程序 MIP。该模型仍保留全部 E2 弧、车辆、容量、时间窗和跨车辆
delivery-before-pickup 约束，不是受限列池。

实现入口：`models/model_4/single_satellite_exact_bp.py` 中的
`solve_single_satellite_direct_program_mip()`。

- 5c C101 回归：`OPTIMAL = 391.57063745`，与 arc-flow 已知最优一致；
- 10c R203，未分区 120s 界优先：全局有效下界 `438.27401570`；
- 10c R203，20s ALNS warm start：可行上界 `516.30058827`；
- 固定车辆数六分区各运行 120s 后，严格组合证书将全局下界提升至
  `441.45078624`；当前区间为 `[441.45078624, 516.30058827]`，
  gap `14.4973%`，仍未证明最优。

```bash
# 界优先完整程序 MIP
python experiments/run_m4_single_satellite_exact_bp.py \
  --scale 10_customers --instance R203 \
  --direct-program-mip --direct-time-limit 120 \
  --pricing-mip-focus 3 --pricing-subtour-strengthening-size 2 \
  --output-file results/m4_single_satellite_exact_bp/10_customers_R203_direct_program_120.json

# ALNS incumbent + 完整程序 MIP
python experiments/run_m4_single_satellite_exact_bp.py \
  --scale 10_customers --instance R203 \
  --direct-program-mip --direct-time-limit 120 \
  --direct-warm-start-alns-time 20 --direct-warm-start-alns-seed 42 \
  --pricing-mip-focus 3 --pricing-subtour-strengthening-size 2 \
  --output-file results/m4_single_satellite_exact_bp/10_customers_R203_direct_program_warm_120.json
```

`proven_optimal=True` 只在完整程序 MIP 返回 `OPTIMAL` 时设置。TIME_LIMIT
下的 `best_bound` 可以作为该单仓子类的全局下界，但不能单独称为最优证明。

### 按 E2 使用车辆数分区

R203 的 E2 体积容量给出至少 2 辆车，配置上限为 7 辆车，因此完整可行域可按
`2,3,4,5,6,7` 辆车分成六个互斥子问题。固定车辆数会消除一部分车辆对称性，
适合分别推进下界。单个分区的下界不是全局下界；覆盖全部六个分区后，应取
六个下界的**最小值**作为全局下界，并取所有 incumbent 的最小值作为全局上界。

```powershell
# k=2 可直接复用并重新验证当前最佳 incumbent（它正好使用 2 辆车）
python experiments/run_m4_single_satellite_exact_bp.py `
  --scale 10_customers --instance R203 `
  --direct-program-mip --direct-fixed-vehicle-count 2 `
  --direct-warm-start-result `
    results/m4_single_satellite_exact_bp/10_customers_R203_direct_program_warm_120.json `
  --direct-time-limit 120 --pricing-mip-focus 3 `
  --pricing-subtour-strengthening-size 2 `
  --output-file results/m4_fixed_vehicle_count/R203_k2.json

3..7 | ForEach-Object {
  $n = $_
  python experiments/run_m4_single_satellite_exact_bp.py `
    --scale 10_customers --instance R203 `
    --direct-program-mip --direct-fixed-vehicle-count $n `
    --direct-time-limit 120 --pricing-mip-focus 3 `
    --pricing-subtour-strengthening-size 2 `
    --output-file "results/m4_fixed_vehicle_count/R203_k${n}.json"
}

python experiments/combine_m4_fixed_vehicle_count_results.py `
  --scale 10_customers --instance R203 `
  --result results/m4_fixed_vehicle_count/R203_k2.json `
  --result results/m4_fixed_vehicle_count/R203_k3.json `
  --result results/m4_fixed_vehicle_count/R203_k4.json `
  --result results/m4_fixed_vehicle_count/R203_k5.json `
  --result results/m4_fixed_vehicle_count/R203_k6.json `
  --result results/m4_fixed_vehicle_count/R203_k7.json `
  --output-file results/m4_fixed_vehicle_count/R203_portfolio.json
```

组合器会从实例容量独立推导必须覆盖的车辆数范围，并拒绝缺失分区、重复分区、
不匹配实例或非固定车辆数结果。只有完整覆盖后，才会输出全局 `best_bound`；
若全局 incumbent 已与该界重合，才设置 `proven_optimal=True`。

R203 的首轮正式分区结果（每个分区 120s）为：

| E2 使用车辆数 | 分区下界 | 分区 incumbent |
| ---: | ---: | ---: |
| 2 | 441.45078624 | 516.30058827 |
| 3 | 455.04459250 | 544.75252775 |
| 4 | 469.65027356 | 596.23121116 |
| 5 | 491.89914695 | 664.45947814 |
| 6 | 538.70847706 | 未找到 |
| 7 | 600.92217508 | 985.86954690 |

六个分区均因 `TIME_LIMIT` 未闭合；取分区下界的最小值和 incumbent 的最小值，
得到全局证书 `LB=441.45078624`、`UB=516.30058827`、gap `14.4973%`。
证书文件为 `results/m4_fixed_vehicle_count/R203_portfolio.json`。

### M3/M4 10c 无时限闭合任务

项目中的 10-customer 数据全部只有一个前置仓，因此 M4 的顾客分配变量被固定，
完整 M3 与 M4 的可行域、E1 成本、E2 程序和最优值完全相同。无时限任务使用
同一个完整单仓程序 MIP 按 E2 实际车辆数分区；每个分区闭合后立即落盘，全部
分区闭合后才分别签发 M3、M4 全局证书。`time_limit=null` 表示没有设置 Gurobi
`TimeLimit`，并非使用一个很大的有限时限。

```powershell
python experiments/run_m3_m4_unlimited_10c.py
```

默认继续处理有限时基准中尚未闭合、且未被既有专门无时限运行补证的实例：
`C209, R103, R201, R203, R205, R207, R209, RC203, RC207, RC208`。
`C109` 已由既有无时限 M3 运行在 `908.842s` 闭合。任务重启时会跳过带有
`subproblem_proven_optimal` 或 `subproblem_proven_infeasible` 证明的分区；限时、
中断或只有 incumbent 的分区不会被误当成完整证书。结果写入
`results/m3_m4_unlimited_10c/<instance>/`，Gurobi 日志和 nodefile 放在纯 ASCII
临时目录 `C:\Windows\Temp\m3_m4_unlimited_10c`，以避开 Gurobi 11 对中文日志
路径的兼容问题。

---

## 双前置仓 arc-flow 精确加速（2026-08-25）

`models/model_4/exact.py` 保留历史基线为默认值，通过
`formulation_options` 显式开启精确等价改写：

- `prune_e2_arcs`：不创建绝对时间窗上不可能的 E2 弧；
- `arc_specific_time_m`：使用任务最晚时间构造逐弧安全大 M；
- `drop_implied_precedence`：回收时间窗已蕴含送收先后关系时，不重复创建跨车辆大 M 约束；
- `route_key_symmetry`：用单一可排序路线键取代单纯初始载重排序；
- `branch_assignment_first`：优先对分仓和前置仓使用决策分支。

统一 A/B 入口为：

```powershell
python experiments/run_m4_exact_acceleration.py `
  --dataset data/zhou_later_pickup/small/standard_same_day/Set1_12-2-4.csv `
  --time-limit 120
```

小规模主批处理入口可以显式选择：

```powershell
python experiments/run_mainline_small_exact.py `
  --groups small --scenarios standard_same_day `
  --models 3 4 --m4-formulation arc_pruning
```

`baseline` 仍是默认值，用于复现历史指纹；`arc_pruning` 是 7/10 顾客推荐值，
`combined` 用于宽时间窗困难实例。

两个前置仓时还提供完全分仓枚举的精确备用方法：

```powershell
python experiments/run_m4_assignment_decomposition.py `
  --dataset data/zhou_later_pickup/small/standard_same_day/Set1_7-2-1b.csv `
  --total-time-limit 600
```

只有全部 `2^|C|` 个固定分仓子问题都返回 `OPTIMAL`/`CUTOFF`/`INFEASIBLE`
时，枚举组合器才设置 `proven_optimal=True`。7 顾客实例的 128 种分仓已全部
完成证明；12 顾客实例在 120s 内只处理 64/4096 种，因此该方法不作为
12 顾客默认求解器。完整 A/B 结果见 `results/m4_exact_acceleration/README.md`。

---

## 50 顾客受限列池求解结果（非全局证明）

### arc-flow MIP 的困难

M4 arc-flow MIP (`models/model_4/exact.py`) 在 50 顾客规模有 **~288,004 个二元变量**（是 M3 的 13.5 倍），因为每个顾客都是每个卫星的候选节点。直接用 Gurobi 求解不可行。

### 路线列重构精确求解器

新实现 `models/model_4/route_pool.py`，用路线列替代 arc-flow：

| 要素 | arc-flow MIP | 路线列 MIP |
|------|-------------|-----------|
| E2 二元变量数 | ~285,628 | ~414 (route columns) |
| 分配变量 | w[c,s] × 200 | w[c,s] × 200 |
| 总二元变量 | ~288,004 | ~614 |
| 求解时间 | 无法求解 | **4.5s（受限列池 OPTIMAL）** |

**列池生成**:
1. Singleton 列: 每个顾客 × 每个卫星 = 400 个单任务路线
2. ALNS 提取列: 从 ALNS 解的 E2 路线中提取 (14 列)
3. 总计 414 列

**关键约束**:
- 分配约束: `Σ_s w[c,s] = 1`
- 覆盖约束: `Σ_{r covers t at s} y[rid] == w[c,s]` (替代 arc-flow 的 `== 1`)
- E1 可选访问: `served_s[s] = 1 iff Σ_c w[c,s] > 0`
- E1 初始载量: `y_lin[c,s,k] = w[c,s] ∧ visits_d[s,k]` 线性化
- 配送截止: `w[c,s]=1 ⇒ A_e1[S{s}d] ≤ tw_del[c]`
- E1-E2 衔接: `S[rid] ≥ A_e1[S{s}d]` (delivery) 和 `A_e1[S{s}p] ≥ R[rid]` (pickup)

**50c C101 受限列池结果**:
- 受限列池目标值: **1285.1435**（低于 M3 已证明最优 1313.0031，因此是可行解而非全局最优）
- Gurobi 状态: OPTIMAL（仅对受限列池）
- MIP Gap: 0.0000%
- 求解时间: 4.5s
- 受限列池解分配: S0:18, S2:8, S5:15, S7:9
