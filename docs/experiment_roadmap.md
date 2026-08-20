# 实验主线：小规模精确对比 + 大规模 ALNS

## 1. 研究目标

本项目的研究目标不是证明所有规模都能精确求解，而是建立两条互补证据链：

1. **小规模精确求解**：证明模型、约束和算法实现是正确的，并给出 ALNS 的最优性缺口；
2. **大规模 ALNS 求解**：证明当精确方法失效时，启发式仍能稳定求解，并突出自由分配、
   取送货和中转机制的管理价值。

一句话概括：

> 小规模解决“对不对”，大规模解决“有没有用”。

## 2. 主线模型

最终建议以 Model 6 / Model 5 为主线，弱化 Model 3 / Model 4：

| 角色 | 模型 | 原因 |
|---|---|---|
| 核心贡献模型 | M6 = M2 + 顾客自由分配 | 直接体现“顾客归属前置仓是决策”的管理价值 |
| 大规模求解主力 | M5 / M6 ALNS | 已有稳定 ALNS 框架和大量实验结果 |
| 精确对照 | M1 / M2 | M1≤50c、M2 50c 可提供可复现精确基准 |
| 机制消融 | M7 = M1 + 自由分配 | 把“自由分配”从取送货机制中剥离出来 |
| 中转叙事 | M5 为主，M3/M4 为历史对照 | M3 精确不可扩展；M4 非全局精确 |

## 3. 实验设计

### 3.1 小规模精确对照实验

目的：

- 验证自由分配确实降低成本；
- 验证 ALNS 与精确最优的差距；
- 检查每个模型的实现一致性。

建议设置：

| 规模 | Exact 基准 | ALNS 对比 | 说明 |
|---|---|---|---|
| 5c | M1、M2 | M2、M6、M7 | 快速全量实验 |
| 10c | M1、M2 | M2、M6、M7 | 核心小规模验证 |
| 20c | 选择性 | M2、M6、M7 | 精确视算例难度选择 |
| 50c | 仅代表算例 | M2、M6、M7 | M1/M2 可证，但不宜全量硬跑 |

关键指标：

1. 精确状态 `proven_optimal`；
2. ALNS 相对精确最优 gap：
   \[
   \text{ALNS gap} = \frac{ALNS - Exact}{Exact}\times 100\%.
   \]
3. 机制价值：
   \[
   \text{Assignment gain} = \frac{M2 - M6}{M2}\times 100\%.
   \]

注意：

- M6/M7 当前路线列结果只能作为 heuristic incumbent；
- 不能把 restricted pool optimum 当作精确基准；
- 若小规模 M6/M7 未全局证明，ALNS gap 应分母使用 M2/M1 或标注为“相对受限池”。

### 3.2 大规模 ALNS 实验

目的：证明 ALNS 的规模能力与机制价值。

建议设置：

| 规模 | 实例 | 模型 | 指标 |
|---|---|---|---|
| 50c | 30 个非北京实例 | M2 / M5 / M6 | 可行率、成本、时间 |
| 100c | 30 个非北京实例 | M2 / M5 / M6 | 可行率、成本、时间 |
| 北京生鲜 | 3 个实例 | M2 / M5 / M6 | 管理场景案例 |

核心指标：

1. 可行率；
2. 平均成本；
3. 多 seed 标准差；
4. 单位时间成本下降；
5. 自由分配收益：
   \[
   \frac{M2 - M6}{M2}.
   \]
6. 中转收益（若保留 M5 叙事）：
   \[
   \frac{M2 - M5}{M2}.
   \]

公平性原则：

- ALNS 按相同时间预算对比，而非相同迭代数；
- M6 每轮评估更重，按迭代数比较会低估其时间成本；
- 多 seed 报告均值 ± 标准差；
- 单模型与 portfolio 分别报告，避免把额外计算时间隐藏在“取最优”中。

### 3.3 消融实验

建议四组：

| 消融 | 对照 | 证明内容 |
|---|---|---|
| 固定分配 vs 自由分配 | M2 vs M6 | 顾客归属决策的价值 |
| 纯配送 vs 取送货 | M7 vs M6 | 回收/取送货机制的额外成本 |
| 无中转 vs 中转 | M2 vs M5 | 中转试探的价值 |
| 单策略 vs portfolio | M5 单策略 vs V6 | 算法鲁棒性 |

### 3.4 精确 vs ALNS 交叉表

小规模结果建议按如下形式呈现：

| 规模 | Exact | M6 ALNS | ALNS gap | Assignment gain |
|---|---:|---:|---:|---:|
| 5c | ... | ... | ... | ... |
| 10c | ... | ... | ... | ... |

大规模结果建议按如下形式呈现：

| 规模 | M2 | M5 | M6 | M6 vs M2 | 可行率 |
|---|---:|---:|---:|---:|---:|
| 50c | ... | ... | ... | ... | ... |
| 100c | ... | ... | ... | ... | ... |

## 4. 当前已有资产

### 可直接复用

- `run_comparison.py`：M1–M4 exact/ALNS 批量框架；
- `experiments/run_model6_vs_model2.py`：M2/M6 ALNS 公平对比；
- `experiments/run_model2345_all_nonbeijing.py`：M2/M3/M4/M5 全量 ALNS 对比；
- `experiments/run_model12_exact_vs_alns_m5.py`：exact vs ALNS vs M5 诊断实验；
- `experiments/run_beijing_alns_comparison.py`：北京场景对比。

### 需要新增的统一实验

当前缺少一个“主线版”统一脚本，建议新增：

```text
experiments/run_mainline_exact_alns.py
```

功能：

1. 小规模跑 M1/M2 exact + M2/M5/M6/M7 ALNS；
2. 中大规模跑 M2/M5/M6/M7 ALNS；
3. 输出：
   - machine-readable CSV/JSON；
   - exact vs ALNS gap 表；
   - assignment gain 表；
   - feasibility 表；
   - 论文图。

建议 CLI：

```bash
PYTHONPYCACHEPREFIX=/tmp/pycache .venv/bin/python \
  experiments/run_mainline_exact_alns.py \
  --small-scales 5_customers 10_customers \
  --large-scales 50_customers 100_customers \
  --alns-time 30 \
  --seeds 42 7 99 \
  --exact-time-small 300 \
  --exact-instances C101 C201 R101 RC101
```

### 需要新增的 ALNS 包装

目前 M6 ALNS 入口与 M2 不同：M2 需要 `e1_tasks/e2_tasks_by_satellite`，
M6 只需要 `problem + vehicles_per_satellite`。为统一实验脚本，建议实现 adapter：

```text
models/model_6/alns_adapter.py
models/model_7/alns_adapter.py
```

返回统一字段：

```python
{
    "success": bool,
    "cost": float | None,
    "elapsed": float,
    "e1_routes": ...,
    "e2_routes_by_sat": ...,
    "assignment": dict | None,
    "moved_customers": int | None,
}
```

## 5. 实施优先级

### 第一阶段：统一口径和输出

1. 新增 M6/M7 ALNS adapter；
2. 新增主实验脚本；
3. 明确 `restricted_pool_optimal` 不进入 Exact 汇总；
4. 输出 CSV/JSON/Markdown 三份一致结果。

### 第二阶段：小规模校准

1. 5c/10c 全实例跑 exact vs ALNS；
2. 计算精确 gap；
3. 抽查路线、成本和分配；
4. 找出 ALNS 最差算例，为算子调优提供依据。

### 第三阶段：中大规模价值实验

1. 50c/100c 跑 M2/M5/M6；
2. 多 seed、多时间预算；
3. 北京案例做场景展示；
4. 输出论文主图和表。

### 第四阶段：算法消融

1. M6 破坏/修复算子消融；
2. M5 portfolio 消融；
3. 不同前置仓数量/容量的敏感性分析。

## 6. 论文叙事建议

建议论文主线写成：

1. **问题背景**：两级生鲜配送 + 前置仓 + 容器回收；
2. **模型贡献**：顾客-前置仓自由分配内生化；
3. **算法贡献**：小规模精确求解 + 大规模 ALNS；
4. **实验设计**：小规模证明正确性，大规模证明价值；
5. **管理启示**：自由分配提高可行性、降低成本，并可自动关闭低利用前置仓。

不要再把“100c 也能精确”作为主要承诺；应把“100c 精确失效而 ALNS 仍稳定”作为算法价值的证据。

## 7. 结论

当前项目的最优路线是：

```text
M1/M2 exact（小规模正确性基准）
        +
M6/M7 机制消融
        +
M5/M6 ALNS（大规模求解能力）
        =
小规模精确对比 + 大规模价值验证
```

M6/M7 的现有路线列结果只能作为启发式 upper bound 或 restricted-pool incumbent，
不能作为全局精确解引用。
