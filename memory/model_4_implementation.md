---
name: model-4-plan
description: Model 4 两阶段求解计划（先规划再中转）
metadata:
  type: project
---

# Model 4 实现记录

## 核心思路

两阶段求解：
1. **阶段一**：求解不中转的最优路线（Model 2 ALNS）
2. **阶段二**：在阶段一路线上，改变 pickup 节点的物理位置（中转），减少总距离

```
阶段一: Model 2 ALNS → 最优路线（无中转）
阶段二: 固定路线 → 改变 pickup 取货位置 → 减少总距离
```

## 中转的定义

中转 = 顾客 A 的空容器可以"搬"到另一个顾客 B 的位置去取。

- pickup 节点 Cp_A **留在原来的路线上**，位置不变
- 只是 Cp_A 的**物理取货位置从 A 变成 B**
- 车不用绕到 A 去取，改在 B 处取，减少行驶距离
- 改变位置后需要重新检查可行性（载量、时间窗）

示例：
- 中转前：车路线 `S → Cd_X → Cp_A(在A处取) → Cd_Y → S`
- 中转后：车路线 `S → Cd_X → Cp_A(在B处取) → Cd_Y → S`

## 目录结构

```
models/model_4/
├── __init__.py          # 导出
├── alns_solver.py       # ALNS 两阶段求解
└── solver.py            # 精确解：Model 2 MIP + 后处理中转
```

## ALNS（alns_solver.py）

### 阶段一
完整运行 Model 2 的 ALNS（无中转算子）：
- destroy: random_removal, worst_removal, shaw_removal
- repair: greedy_insertion, regret_insertion
- 收敛到最优路线

### 阶段二（后处理中转）
对阶段一的最优解，迭代搜索 pickup 位置变更机会：

```
repeat:
  best_plan = None
  对每个卫星下的每个 pickup 节点 Cp_A（物理位置 A）:
    对同卫星下每个其他顾客位置 B:
      临时将 Cp_A 的物理位置从 A 改为 B
      检查路线可行性（载量 + 时间窗）
      计算路线距离变化 net_saving
      if net_saving > best_plan:
        更新 best_plan
      恢复物理位置
  if best_plan 且 net_saving > 0:
    应用：永久改变 Cp_A 的物理位置
    记录中转信息
  else:
    break
```

## 精确解（solver.py）

1. 阶段一：调用 Model 2 的 MIP
2. 阶段二：将 MIP 结果转为 Solution，运行同样的后处理中转

## 已修改的文件

1. `models/model_4/__init__.py` — 新建
2. `models/model_4/alns_solver.py` — 新写：Model 2 ALNS + 后处理中转
3. `models/model_4/solver.py` — 新写：Model 2 MIP + 后处理中转
4. `utils/task_builder.py` — 添加 model 4 支持
5. `config/app_config.json` — 添加 model_4 选项
6. `main.py` — 添加 model_4 分支

## transshipments 格式

```python
# {(sat_id, route_idx, pick_node, new_phys): (old_phys, dq, dw)}
# sat_id: 卫星 ID
# route_idx: pickup 所在路线索引
# pick_node: pickup 节点 ID
# new_phys: 新的取货物理位置
# old_phys: 原始取货物理位置
# dq, dw: 容器重量和体积
```

## 实现细节

- Evaluator 通过 `task_to_phys` 字典映射节点到物理位置
- 阶段二中临时修改 `task_to_phys[pick_node]` 来模拟位置变更
- 应用后永久修改，影响后续的距离和可行性计算
- `check_e2_route_feasible` 会用新位置重新计算所有行驶时间和时间窗

## 运行结果

数据集：100_customers/R101.csv，seed=42，5000 次迭代

- 阶段一（Model 2 ALNS 无中转）：3412.90
- 阶段二（后处理中转）：**3258.60**（改进 154.31）
- 中转操作数：29 个 pickup 改变了取货位置
- 总耗时：22.37 秒（阶段二几乎不增加时间）

## 对比

| | Model 2 | Model 3 ALNS | Model 4 ALNS |
|---|---|---|---|
| 中转 | 无 | 迭代中搜索 | 后处理搜索 |
| 结果 | 3412.90 | ~2350.35* | **3258.60** |
| 改进 | - | 比Model 2差 | 比Model 2好 |

*注：Model 3 使用不同的数据集/参数
