---
name: model-fix
description: Model 5 ALNS 中转逻辑重写记录（已完成）
metadata:
  type: project
---

# Model 5 ALNS 中转逻辑重写

## 核心概念

中转 = 移动取货点位置（取货时间随之变化）→ 减少整体行驶距离。

精确解已正确实现，只改 ALNS。

## 修改内容（已完成）

文件：`models/model_5/alns_solver.py`

### 1. transshipments 格式
```
旧: {(sat_id, a_idx, b_idx, pick_node): (cw, cv)}
新: {(sat_id, carrier_idx, pick_node, drop_node): (dq, dw)}
```
- carrier 在 pick_node（位置 X）取容器，带到 drop_node（位置 D）放下
- 其他经过 D 的车在 D 处接收容器

### 2. optimize_transshipment 重写为接力逻辑
- 对每个 pickup 节点 P，找候选搬运车 A
- A 插入 P 到路线，在 P 之后找共享节点 D（D 的物理位置也在其他车路线上）
- carrier 在 D 处 trans_out，其他车在 D 处 trans_in
- 只在净收益 > 0 时应用

### 3. _build_transshipment_maps 适配新格式
- carrier → trans_out at phys(drop_node)
- 经过 phys(drop_node) 的其他车 → trans_in

### 4. transshipment_destroy 适配新格式
- 从 carrier 路线移除 pick_node
- 贪心插入最优路线

### 5. _print_solution 适配新格式

## 不改的部分
- 精确解（model.py, constraints.py, solver.py, objective.py）
- ALNS 的 destroy/repair 算子和主循环结构
