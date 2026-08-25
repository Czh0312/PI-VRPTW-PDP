# 精确求解结果（10 / 50 / 100 顾客）

> **2026-08-17 口径修正**：M4/M2 的路线列主问题来自 singleton + ALNS 提取列的**受限列池**。
> Gurobi 对该受限池返回 `OPTIMAL`，只表示 `restricted_route_pool_optimal=true`，不能证明
> 全局最优。原因见 [`column_generation_m4_m2_diagnostic.md`](column_generation_m4_m2_diagnostic.md)：
> 当前 pricing 公式遗漏了路线 timing、vehicle-usage 和 E1/E2 linking 约束的对偶贡献。
> 因此本文下方 M4/M2 旧标题中的 “PROVEN OPTIMAL” 已经失效，只能按“受限列池最优”解读。

## 概述

本项目实现了 2E-VRPTW-PDD（两阶段带时间窗的取送货车辆路径问题）在 **C101 算例**上的**精确求解**，
覆盖 M3 与 M4/M2 的代表性结果。**M3 的 50c 为精确证明；M4/M2 为受限列池最优，不是全局精确证明。**

| 模型 | 描述 | 方法 | 规模 | 目标值 | 求解时间 | 状态 |
|------|------|---------|------|--------|---------|------|
| **M3** | 固定分配 PD 模型 | 路线列主问题 + BCP 下界 | 50c | 1313.0031 | 46.4s | ✓ PROVEN OPTIMAL |
| **M3** | 固定分配 PD 模型 | E1 前沿枚举 + floor 证书分解 | 100c | 1829.0094 | ~20 min | 证书路径，非直接求解口径 |
| **M4** | 自由分配 PD 模型（w[c,s] 决策） | 受限路线列池 | 50c | 1285.1435 | 4.5s | restricted-pool optimal |
| **M4** | 自由分配 PD 模型 | 受限路线列池 | 100c | 1867.9501 | 101.5s | restricted-pool optimal |
| **M2** | 自由分配纯配送模型 | 受限路线列池 | 10c | 474.8616 | 0.0s | restricted-pool optimal |
| **M2** | 自由分配纯配送模型 | 受限路线列池 | 50c | 1127.5179（另有可行解 1118.5517） | 5.7s | restricted-pool optimal |
| **M2** | 自由分配纯配送模型 | 受限路线列池 | 100c | 1566.9827 | 220.6s | restricted-pool optimal |

---

## 模型说明

| 模型 | 取送货 | 顾客-前置仓分配 | E2 任务数(100c) |
|------|:------:|:--------------:|:---------------:|
| **M3** | ✓ (配送+回收) | 固定 (数据输入) | 200 (100d+100p) |
| **M4** | ✓ (配送+回收) | **自由 (w[c,s] 决策)** | 200 (100d+100p) |
| **M2** | ✗ (仅配送) | **自由 (w[c,s] 决策)** | 100 (100d) |

- **M3 vs M4**：唯一区别是顾客归属前置仓是固定还是决策变量。M4 解空间是 M3 的严格超集。
- **M4 vs M2**：唯一区别是 M2 无取送货（只有配送，无容器回收）。M2 的 E2 任务数减半，模型更小。
- **M2 = M1 + M4 的分配机制**：M2 = Model 1（纯配送）+ Model 4（自由分配 w[c,s]）。

---

## 50 顾客结果

### M3: 固定分配模型 — ✓ PROVEN OPTIMAL

**方法**: 路线列主问题（route-pool master）
- 用 **per-satellite CG** 生成 E2 路线列
- 注入 **BCP 卫星下界**（`E2_SatCostLB` 约束）强化 LP 松弛
- 用 **ALNS 解**作为 Gurobi warm start
- Gurobi 在 46.4s 内证明最优

**数据**:
- ALNS 初始解: 1322.59
- 路线列数: 894 (4 satellites × per-satellite CG)
- BCP 卫星下界: S0=286.43, S2=119.39, S5=215.18, S7=151.83
- **最优目标值: 1313.0031** (E1 + E2 总距离)
- MIP Gap: 0.0000%

### M4: 自由分配模型 — 受限列池最优（非全局证明）

**方法**: 路线列重构（替代 arc-flow MIP）
- 用 `w[c,s]` 二元分配变量替代 E2 arc-flow 的 ~285k 个二元变量
- 列池: singleton 列 + ALNS 提取列
- E1 可选访问、E1 载量线性化、配送截止时间蕴含式
- Gurobi 在 4.5s 内证明受限列池最优

**数据**:
- ALNS 初始解: 1333.56
- 路线列数: 414 (singleton + ALNS-extracted)
- **受限列池目标值: 1285.1435**（可行 upper bound）
- MIP Gap: 0.0000%
- 受限列池解分配: S0:18, S2:8, S5:15, S7:9

### M2: 自由分配纯配送模型 — 受限列池最优（非全局证明）

**方法**: 路线列重构 + 自由分配（同 M4 但无取送货）
- E2 任务数减半（仅配送，无回收），模型更小更快
- Gurobi 在 5.7s 内证明受限列池最优

**数据**:
- ALNS 初始解: 1175.94
- 路线列数: 212
- **受限列池目标值: 1127.5179**（后续找到更好可行解 1118.5517，说明列池不完整）
- MIP Gap: 0.0000%
- 受限列池解分配: S0:19, S2:5, S5:17, S7:9

---

## 100 顾客结果

### M3: 固定分配模型 — ✓ PROVEN OPTIMAL (via decomposition)

**方法**: E1 前沿枚举 + floor 证书分解
- M3 100c 的 arc-flow MIP 和直接路线列主问题无法在合理时间内闭合
- 采用分解证明路径：
  1. **E1 前沿枚举**：枚举所有 Pareto 最优 E1 计划（18 个计划，3 个 Pareto 类）
  2. **BCP 卫星下界**：每个卫星的 E2 最优下界（总计 1031.88）
  3. **floor 证书**：对每个 E1 计划，验证 E2 ≥ BCP 下界，确认 C0 = E1_best + E2_LB ≥ incumbent
- **最优目标值: 1829.0094**
- 前沿完整: True (18 plans, 3 Pareto classes)
- E1 最优: 797.13, E2 下界: 1031.88

**BCP 卫星下界明细**:
| 卫星 | S0 | S1 | S2 | S3 | S4 | S5 | S6 | S7 | 合计 |
|------|-----|-----|-----|-----|-----|-----|-----|-----|------|
| 下界 | 180.70 | 198.22 | 117.49 | 163.62 | 88.55 | 107.98 | 139.70 | 35.62 | 1031.88 |

### M4: 自由分配模型 — 受限列池最优（非全局证明）

**方法**: 路线列重构 + 自由分配
- 100 顾客 × 8 卫星的自由分配，路线列主问题直接求解
- Gurobi 在 101.5s 内证明受限列池最优

**数据**:
- ALNS 初始解: 1879.55
- 路线列数: 814
- **受限列池目标值: 1867.9501**
- MIP Gap: 0.0000%
- 受限列池解分配: S0:13, S1:19, S2:9, S3:20, S4:10, S5:8, S6:14, S7:7

### M2: 自由分配纯配送模型 — 受限列池最优（非全局证明）

**方法**: 路线列重构 + 自由分配（同 M4 但无取送货）
- E2 任务数减半（100d vs 200d），模型更紧凑
- Gurobi 在 220.6s 内证明受限列池最优

**数据**:
- ALNS 初始解: 1644.46
- 路线列数: 823
- **受限列池目标值: 1566.9827**
- MIP Gap: 0.0000%
- 受限列池解分配: S0:17, S1:19, S2:9, S3:16, S4:10, S5:8, S6:21, S7:0

---

## 10 顾客结果（M2 验证）

### M2: 自由分配纯配送模型 — 受限列池最优（非全局证明）

- ALNS 初始解: 474.86
- 路线列数: 14
- **受限列池目标值: 474.8616**
- 求解时间: 0.0s (Presolve 直接消除)
- 受限列池解分配: S0:10（10 顾客仅 1 卫星）

---

## 为什么 arc-flow MIP 不能直接求解

### M3 arc-flow（50c）
- **23,852 个变量**（21,396 个二元变量），**110,877 个约束**
- LP 松弛间隙 18.9%（LP=1045.44 vs incumbent~1289-1356）
- Gurobi branch-and-bound 在 600s 内无法找到可行解

### M4 arc-flow（50c）
- **~288,004 个二元变量**（是 M3 的 13.5 倍），因为每个顾客都是每个卫星的候选
- 弧流变量爆炸

### 路线列重构的优势

| 对比项 | arc-flow MIP | 路线列主问题 |
|--------|-------------|-------------|
| E2 变量 | z[i,j,v] 弧变量（~285k） | y[rid] 路线变量（~200-823） |
| 时间窗约束 | Big-M 松弛 | 路线内预计算（紧致） |
| LP 间隙 | 18.9% (M3 50c) | <1% |
| 100c 求解 | 不可行 | M4: 101.5s, M2: 220.6s（仅列池内） |

关键思路：用路线列变量 `y[rid]` 替代 E2 弧变量 `z[i,j,v]`，每条路线已包含时间窗、容量、
先送后取约束，不再需要 Big-M。配合 `w[c,s]` 自由分配变量和 singleton 列 + ALNS 提取列，
模型大小从 ~285k 二元变量降至 ~600-1600 二元变量。

---

## 代码结构

```
models/model_3/
  route_pool_exact.py    -- M3 路线列主问题
  e2_route_cg.py          -- per-satellite CG
  exact_decomposition.py  -- 100c 分解证明路径
  solver.py               -- arc-flow MIP + warm start

models/model_4/
  route_pool.py           -- M4 路线列主问题（自由分配 + 取送货）
  exact.py                -- M4 arc-flow MIP（仅小规模）
  alns_solver.py          -- M4 ALNS

models/model_2/
  route_pool.py           -- M2 路线列主问题（自由分配 + 纯配送）
  __init__.py

experiments/
  run_50c_exact_m3_m4.py  -- 50c/100c 精确求解实验脚本
  run_exact_m3_decomposition.py -- 100c M3 分解证明脚本
```

## 结果口径汇总

| 模型 | 10c | 50c | 100c | 结论 |
|---|---:|---:|---:|---|
| M1 | 已证明 474.86 | 已证明 1107.75 | 未证明 | ≤50c 可作为精确 baseline |
| M3 | 已证明 543.09 | 已证明 1313.0031 | 证书路径 1829.0094（非直接求解口径） | 核心精确对照 |
| M4 | 543.09（与 M3 相同） | 1285.1435 受限列池 | 1867.9501 受限列池 | 只能作 heuristic incumbent |
| M2 | 474.8616 受限列池 | 1127.5179/1118.5517 受限列池 | 1566.9827 受限列池 | 只能作 heuristic incumbent |

**为什么 M2 50c 有两个数？**
`1127.5179` 是原 singleton + ALNS 列池的最优；后续诊断列生成找到了 `1118.5517` 的更好可行解。
两者都不是全局最优证明，但后者说明原列池确实不完整。

## 复现方法

```bash
# 50c M3 + M4
PYTHONPYCACHEPREFIX=/tmp/pycache .venv/bin/python \
    experiments/run_50c_exact_m3_m4.py \
    --scale 50_customers --alns-time 90 --m3-time 600 --m4-time 600

# 100c M4
PYTHONPYCACHEPREFIX=/tmp/pycache .venv/bin/python \
    experiments/run_50c_exact_m3_m4.py \
    --scale 100_customers --skip-m3 --m4-time 600 --alns-time 90

# 100c M3 分解证明
PYTHONPYCACHEPREFIX=/tmp/pycache .venv/bin/python \
    experiments/run_exact_m3_decomposition.py \
    --scale 100_customers --instance C101 \
    --columns-json reference/exact_proof_evidence/merged_columns/100_customers__C101__columns.json \
    --satellite-lb-json reference/exact_proof_evidence/exact_incumbents/m3_100_c101_satellite_bcp_exact_lbs.json \
    --incumbent 1829.0093866572092 \
    --output-dir output/exact_m3_decomp
```

输出目录:
- `output/exact_50c_m3_m4/{scale}__C101/m3_result.json`
- `output/exact_50c_m3_m4/{scale}__C101/m4_result.json`
- `output/exact_50c_m3_m4/{scale}__C101/m2_result.json`
- `output/exact_m3_decomp/100_customers__C101/verdict.json`（M3 100c 分解证明）

## 结果汇总表

带 `*` 的数值是 restricted route-pool incumbent，不是全局最优。

| 模型 | 10c | 50c | 100c | 自由分配 | 取送货 |
|------|-----|-----|------|:--------:|:------:|
| **M3** | — | 1313.00 (46.4s) | 1829.01 (~20min) | ✗ | ✓ |
| **M4** | — | 1285.14* (4.5s) | 1867.95* (101.5s) | ✓ | ✓ |
| **M2** | 474.86* (0.0s) | 1127.52* (5.7s) | 1566.98* (220.6s) | ✓ | ✗ |

**关键发现**：
- *M4/M2 为受限列池可行解。50c 上 M4 可行解比 M3 已证明最优低 2.1%，说明自由分配有收益，但 M4 全局最优仍未证明
- 纯配送（M2 vs M4）成本更低，因为无回收任务和 E1-E2 衔接约束
- M2 100c 受限列池解中 S7 被关闭（0 顾客分配），说明自由分配能自动优化前置仓开关
