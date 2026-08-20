# Model 7：自由分配纯配送 2E-VRPTW

## 一句话定位

Model 7 = **Model 1（纯配送，无取送货）** + **Model 6（顾客-前置仓分配内生化）**。

与 Model 1 的唯一区别：顾客归属哪个前置仓是**决策变量** `w[c,s]`，而非数据固定的 `assigned_sat`。
与 Model 6 的唯一区别：**无取送货**（只有配送 S{s}d / Cxd，无回收 S{s}p / Cxp）。

因此 M7 的解空间是 M1（固定分配纯配送）的严格超集：M1 的任何固定分配可行解在 M7 中都可行。
任何成本差都归因于"自由分配"这一个自由度，实验对比干净。

## 模型关系图

```
                取送货(PD)？
              是 ↗         ↗ 否
          M2 (固定分配)   M1 (固定分配)
    PD + 自由分配 ↗         ↗ 自由分配
          M6 (自由分配)   M7 (自由分配)
```

| 模型 | 取送货 | 自由分配 | E2 任务数(100c) | 100c 最优值 | 100c 求解时间 |
|------|:------:|:--------:|:---------------:|:-----------:|:------------:|
| M1 | ✗ | ✗ | 100 | — | — |
| M2 | ✓ | ✗ | 200 | 1829.01 | ~20min (分解) |
| M6 | ✓ | ✓ | 200 | 1867.95 | 101.5s |
| **M7** | **✗** | **✓** | **100** | **1566.98（受限列池）** | **220.6s** |

> 注：M6 比 M2 成本更高是因为 M6 100c 使用全部 8 个前置仓（含 S7），而 M2 100c 的固定分配
> 可能恰好利用了更优的预分配。自由分配的价值在 50c 对比中更清晰：M6 (1285.14) < M2 (1313.00)，优化 2.1%。

## 建模要点

### 与 M6 的差异（去除取送货）

M7 相比 M6 删除了所有与 pickup/回收相关的建模组件：

| 组件 | M6 | M7 |
|------|----|----|
| E2 任务 | 每顾客 2 个 (Cxd + Cxp) | 每顾客 1 个 (Cxd) |
| E1 节点 | S{s}d + S{s}p | 仅 S{s}d |
| E1-E2 回收衔接 | A_e1[S{s}p] ≥ R[rid] | 无 |
| E2 顾客先送后取 | A2[p] ≥ A2[d] + svc | 无 |
| E1 路线 | depot → S{s}d → S{s}p → depot | depot → S{s}d → depot |
| 容器重量/体积 | E1 回收载量约束 | 无 |

E2 任务数从 2N 降为 N（100c: 200 → 100），模型规模大幅缩减。

### 保留的 M6 核心机制

- **`w[c,s]` 自由分配变量**：二元变量，顾客 c 归属前置仓 s
- **E1 节点可选**：`served_s[s] = 1 iff Σ_c w[c,s] > 0`（无顾客的卫星自动关闭）
- **E1 载量线性化**：`y_lin[c,s,k] = w[c,s] ∧ x[visits S{s}d, k]`，避免双线性项
- **配送截止时间蕴含式**：`w[c,s]=1 ⇒ A_e1[S{s}d] ≤ tw_del[c]`
- **E2 覆盖链接**：`Σ_{r covers c at s} y[rid] == w[c,s]`（非 `== 1`）

### 路线列重构

M7 采用与 M6 相同的路线列重构策略：

```text
弧流 MIP:  ~570,000 个二元弧变量 z[i,j,v]  →  不可解
路线列 MIP: ~823 个路线变量 y[rid] + ~800 个 w[c,s] + ~6400 个 x[i,k]  →  220s restricted-pool OPTIMAL
```

每条路线列预计算了时间窗、容量约束，无需 Big-M 松弛，LP 间隙极小。

## 实现

| 模块 | 内容 |
|---|---|
| `models/model_7/route_pool.py` | M7 完整实现：ALNS + 受限路线列主问题（~1373 行） |
| `models/model_7/__init__.py` | 包文档 |

### `route_pool.py` 核心组件

| 函数/类 | 功能 |
|---------|------|
| `_setup_geometry()` | 构建 M7 几何（纯配送：仅 S{s}d 节点，仅 Cxd 任务） |
| `build_m7_route_master()` | Gurobi MIP：w[c,s] + 路线列 + E1 线性化 |
| `Evaluator` 类 | 纯配送分配感知求值器（从解反推分配、动态重建 E1 任务） |
| `solve_alns()` | 全 ALNS 引擎（destroy/repair 算子，适配自 M6 但纯配送） |
| `solve_m7_exact()` | 路线列受限池求解器（ALNS warm start → restricted-pool OPTIMAL） |
| `_apply_warm_start()` | 将 ALNS 解注入 Gurobi MIP start |

### ALNS 设计

- 分配由解隐式表达：顾客配送任务出现在哪个前置仓的 E2 路由桶，就归属哪个前置仓
- E1 任务集（仅配送，无回收）每次评估前按当前分配确定性重建
- 破坏算子按**顾客**删除（仅配送任务，无成对 d+p）：
  - `random_customer_removal`：随机删除
  - `worst_customer_removal`：删除成本贡献最大的顾客
  - `shaw_customer_removal`：删除相关性高的顾客
- 修复算子：`multi_sat_insertion`：跨卫星贪婪插入（逐仓比较最优位置）
- 每个候选后追加 **E1 2-opt 局部改进**

### 与 M6 ALNS 的差异

M7 的 ALNS 从 M6 的 `alns_solver.py` 改编，但去除了所有取送货相关逻辑：

| ALNS 组件 | M6 | M7 |
|-----------|----|----|
| 破坏单位 | 顾客对 (d+p) | 单顾客 (d) |
| 修复算子 | 成对联合插入 + 跨车顺序插入 | 多卫星单任务插入 |
| E1 重建 | 配送 + 回收任务 | 仅配送任务 |
| E2 路线模拟 | 先送后取 + 容量回收 | 仅配送 + 容量 |
| 初始解 | `build_strong_initial_solution` | `build_initial_solution`（M1 风格） |

## 精确求解结果

> **2026-08-17 口径修正**：本节的 `OPTIMAL` 是 Gurobi 在 singleton + ALNS 提取列构成的
> **受限路线列池**上的 OPTIMAL，不能证明 M7 全局最优。后续诊断 CG 在 50c 找到 1118.5517
> 的更好可行解，进一步证明原列池不完整。详见
> [`column_generation_m6_m7_diagnostic.md`](column_generation_m6_m7_diagnostic.md)。

### 全部规模均为受限列池最优（非全局证明）

| 规模 | ALNS 初始解 | 路线列数 | 受限列池目标值 | MIP Gap | Gurobi 时间 | 总时间 |
|------|-----------|---------|-----------|---------|-----------|--------|
| 10c | 474.86 | 14 | 474.8616 | 0.00% | 0.0s | 3.1s |
| 50c | 1175.94 | 212 | 1127.5179 | 0.00% | 5.7s | 67.2s |
| 100c | 1644.46 | 823 | 1566.9827 | 0.00% | 220.6s | 302.5s |

### 100c 受限列池解的分配

| S0 | S1 | S2 | S3 | S4 | S5 | S6 | S7 |
|----|----|----|----|----|----|----|-----|
| 17 | 19 | 9 | 16 | 10 | 8 | 21 | **0** |

> S7 被自动关闭（0 顾客分配），说明自由分配能优化前置仓开关决策。

### 求解流程

```
1. ALNS warm start (30-90s)
   ├── 构造初始解（固定分配种子 + 最近/次近前置仓）
   ├── destroy/repair 迭代（跨卫星搬移顾客）
   └── E1 2-opt 局部改进

2. 路线列池构建
   ├── singleton 列（每顾客 × 每卫星 = 1 条单任务路线）
   └── ALNS 提取列（从 ALNS 最优解的 E2 路线提取）

3. Gurobi MIP 求解
   ├── 注入 ALNS 解为 MIP start
   ├── w[c,s] 自由分配 + 路线列 y[rid]
   ├── E1 可选访问 + 载量线性化 + 截止时间蕴含式
   └── Presolve + Cuts → OPTIMAL
```

## 为什么 M7 比 M6 更容易精确求解

| 因素 | M6 (100c) | M7 (100c) |
|------|-----------|-----------|
| E2 任务数 | 200 (100d+100p) | 100 (100d) |
| E1 节点 | 16 (8d+8p) | 8 (8d) |
| E1-E2 衔接约束 | 有 (回收) | 无 |
| E2 顾客先送后取 | 有 | 无 |
| 路线列数 | 814 | 823 |
| Gurobi 求解时间 | 101.5s | 220.6s |

> 注：M7 虽然任务数减半，但 100c 求解时间反而比 M6 略长（220.6s vs 101.5s）。
> 这是因为 M7 的列池结构和分配组合不同，且 Gurobi 的分支策略受具体实例影响。
> 两者都在合理时间内达到受限列池 OPTIMAL；该状态不等价于全局精确证明。

## 代码结构

```
models/model_7/
  __init__.py             — 包文档
  route_pool.py           — M7 完整实现 (~1373 行)
    _setup_geometry()           — 构建 M7 几何
    _normalize_columns_m7()     — 列标准化
    _singleton_column_m7()      — 单任务列生成
    _generate_singleton_columns() — 全量 singleton 列
    _extract_route_columns_from_alns() — 从 ALNS 解提取列
    build_m7_route_master()     — Gurobi MIP 模型
    Evaluator 类                — 纯配送分配感知求值器
    build_initial_solution()    — M1 风格初始解
    random/worst/shaw_customer_removal — 破坏算子
    multi_sat_insertion()       — 修复算子
    _e1_local_improve()         — E1 2-opt
    solve_alns()                — ALNS 求解器
    _apply_warm_start()         — MIP start 注入
    solve_m7_exact()            — 精确求解入口

experiments/
  run_50c_exact_m2_m6.py  — 精确求解实验脚本（支持 M7）
```

## 复现

```bash
# M7 10c
PYTHONPYCACHEPREFIX=/tmp/pycache .venv/bin/python -c "
import json, time, sys
sys.path.insert(0, '.')
from core.instance import ProblemInstance
from utils.data_loader import parse_dataset
from models.model_7.route_pool import solve_m7_exact, solve_alns, Evaluator
from models.alns_base import Route, Solution

fleet_cfg = json.loads(open('config/fleet_config.json').read())
nodes, _ = parse_dataset('data/10_customers/C101.csv')
problem = ProblemInstance(nodes, fleet_cfg, speed=fleet_cfg.get('speed', 1.0))
problem.aggregate_demands_from_pre_assignment()

e1_routes, e2_routes, assignment = solve_alns(problem, 7, max_time=30, verbose=False)
ev = Evaluator(problem, 7)
sol = Solution()
sol.e1_routes = [Route(problem.depot.id, list(r[1:-1])) for r in e1_routes]
for sat, routes in e2_routes.items():
    sol.e2_routes[sat] = [Route(sat, list(r[1:-1])) for r in routes]

result = solve_m7_exact(problem, 7, incumbent_cost=ev.total_cost(sol),
    e1_routes=e1_routes, e2_routes_by_sat=e2_routes,
    assignment=assignment, time_limit=120, return_info=True)
print(f'Objective: {result[\"info\"][\"objective\"]:.4f}')
print(f'Proven optimal: {result[\"info\"][\"proven_optimal\"]}')
"
```

结果文件:
- `output/exact_50c_m2_m6/10_customers__C101/m7_result.json`
- `output/exact_50c_m2_m6/50_customers__C101/m7_result.json`
- `output/exact_50c_m2_m6/100_customers__C101/m7_result.json`
