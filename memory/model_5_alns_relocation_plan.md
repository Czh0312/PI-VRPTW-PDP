---
name: model-5-fix
description: Model 5 ALNS 中转逻辑改为"改变取货位置"（与 Model 6 一致）
metadata:
  type: project
---

# Model 5 ALNS 中转逻辑修改计划

## 背景

Model 5 ALNS 当前的中转机制是"接力中转"：carrier 去取容器，带到共享节点放下，另一辆车取。
问题：
1. 需要两条路线有物理位置交叉点，很多情况下找不到
2. 需要 trans_in/trans_out 载量追踪，逻辑复杂
3. 与 ALNS 迭代互相干扰收敛

修改目标：改为和 Model 6 一样的"改变取货位置"机制。

## 中转定义（与 Model 6 一致）

中转 = 把顾客 A 的 pickup 物理位置从 A 换成另一个顾客 B 的位置，车直接在 B 处取。

- pickup 节点留在原来的路线上
- 只改变物理取货位置
- 不要求路线交叉

## 修改文件

`models/model_5/alns_solver.py` — 唯一需要修改的文件

## 具体改动

### 1. Solution.transshipments 格式

```python
# 旧
{(sat_id, carrier_idx, pick_node, drop_node): (dq, dw)}
# 新
{(sat_id, route_idx, pick_node, new_phys): (old_phys, dq, dw)}
```

### 2. Evaluator 简化

- `check_e2_route_feasible`：移除 trans_in/trans_out 参数，改为标准检查（同 Model 3）
- `_build_transshipment_maps`：**删除**（不再需要）
- `is_feasible`：移除 transshipment map 构建，用标准 check_e2_route_feasible

### 3. optimize_transshipment 重写

改为和 Model 6 的 `_search_and_apply_relocation` 一样的逻辑：

```
对每个卫星下的每个 pickup 节点 Cp_A（物理位置 A）:
  对同卫星下每个其他顾客位置 B:
    临时将 task_to_phys[Cp_A] 从 A 改为 B
    检查路线可行性（载量 + 时间窗）
    计算路线距离变化 net_saving
    if net_saving > best_plan:
      更新 best_plan
    恢复物理位置
if best_plan 且 net_saving > 0:
  应用：永久改变 Cp_A 的物理位置
  记录中转信息
```

### 4. transshipment_destroy 重写

改为撤销 pickup 位置变更：

```
对每个被撤销的 transshipment (sat_id, route_idx, pick_node, new_phys):
  将 task_to_phys[pick_node] 改回 old_phys
  删除 transshipment 记录
```

### 5. _print_solution 适配

适配新的 transshipments 格式打印中转信息：
- 打印 pickup 节点、原始位置 → 新位置

### 6. 载量模拟简化

- `_simulate_e2_load`：移除 trans_in/trans_out 参数，用标准载量计算

## 不改的部分

- 精确解（model.py, constraints.py, solver.py, objective.py）不动
- ALNS 的 destroy/repair 算子（random_removal, worst_removal, shaw_removal, greedy_insertion, regret_insertion）不动
- ALNS 主循环结构不动（仍然在迭代中调用 optimize_transshipment）
- build_initial_solution 不动

## 与 Model 6 的区别

| | Model 5 | Model 6 |
|---|---|---|
| 中转定义 | 改变取货位置（同） | 改变取货位置（同） |
| 中转时机 | ALNS 迭代中 | ALNS 完成后（后处理） |
| transshipment_destroy | 有（迭代中撤销） | 无（不需要） |
| 结果保证 | 无保证（中转仍干扰收敛） | ≤ Model 3 |

## 验证

1. Model 5 ALNS 结果应比之前好（不再受接力机制限制）
2. 中转记录应显示 pickup 位置变更（不再是 carrier/drop_node）
3. 精确解不受影响
