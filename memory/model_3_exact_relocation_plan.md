---
name: model-3-exact-fix
description: Model 3/4 中转逻辑修改计划：搬运车取放容器 + 取货车改位置（三处统一修改）
metadata:
  type: project
---

# 中转逻辑统一修改计划（Model 3 精确 + Model 3 ALNS + Model 4）

## 正确的中转模型

```
示例：顾客 X 的空容器搬运到 Y 位置

搬运车 V1 原路线：S → 1 → 4 → 6 → 8 → S
搬运车 V1 改路线：S → 1 → X(取容器) → 4 → 6 → Y(放容器) → 8 → S
                        ↑ 绕路取            ↑ 绕路放（绕路代价）

取货车 V2 原路线：... → Cp_X(在X处取) → ...
取货车 V2 改路线：... → Cp_X(在Y处取) → ...  （节省距离）

净收益 = V2 节省距离 - V1 绕路代价
```

---

## 需要修改的三个位置

### 1. Model 3 精确求解（MIP）
- `models/model_3/model.py` — 删除接力中转变量
- `models/model_3/constraints.py` — 删除接力中转约束
- `models/model_3/solver.py` — 添加后处理搬运搜索

### 2. Model 3 ALNS
- `models/model_3/alns_solver.py` — 重写 optimize_transshipment

### 3. Model 4（ALNS + 精确求解共用）
- `models/model_4/alns_solver.py` — 重写 _search_and_apply_relocation
- `models/model_4/solver.py` — 不需改（调用 alns_solver 的 postprocess）

---

## 共享的搬运搜索算法

三处共用同一个搜索逻辑，建议提取到共享模块 `utils/transport_search.py`：

### 算法

```
对每个卫星：
  对每个路线中的每个 pickup 节点 Cp_X（位置 X）：
    if Cp_X 已被中转: continue

    V2_old_dist = V2 当前路线距离（Cp_X 在位置 X）

    对每个候选位置 Y（同卫星其他顾客位置）：
      # 1. 计算 V2 节省
      临时 task_to_phys[Cp_X] = Y
      if V2 路线不可行: 恢复, continue
      V2_new_dist = V2 新路线距离
      V2_saving = V2_old_dist - V2_new_dist
      恢复 task_to_phys[Cp_X]

      if V2_saving <= 0: continue

      # 2. 找最优搬运车 V1 和最优插入位置
      对同卫星的每辆车 V1（排除 V2）：
        对 V1 路线中每个位置 pick_pos (0..len)：
          计算 V1 在 pick_pos 插入"停靠X"的绕路代价 detour_pick

          对 V1 路线中 pick_pos 之后的每个位置 drop_pos：
            计算 V1 在 drop_pos 插入"停靠Y"的绕路代价 detour_drop
            detour_cost = detour_pick + detour_drop

            # 检查 V1 可行性
            临时插入两个停靠
            if V1 路线可行（载量 + 时间窗）：
              net_saving = V2_saving - detour_cost
              更新 best_plan
            恢复 V1 路线

  if best_plan 且 net_saving > 0:
    应用：V2 的 Cp_X 改位置，V1 插入停靠
    记录 transshipment
```

### 搬运车停靠点的表示

在路线 nodes 中插入虚拟节点：
- `TRANS_PICK_X`：在位置 X 取容器（载量 +w, +v）
- `TRANS_DROP_Y`：在位置 Y 放容器（载量 -w, -v）

task_to_phys 映射：
- `TRANS_PICK_X` → X 的物理位置
- `TRANS_DROP_Y` → Y 的物理位置

可行性检查需要识别虚拟节点：
```python
for nid in route.nodes:
    if nid.startswith("TRANS_PICK_"):
        Q_load += container_w; V_load += container_v
        # 时间：行驶到 X 位置 + service_time(X)
    elif nid.startswith("TRANS_DROP_"):
        Q_load -= container_w; V_load -= container_v
        # 时间：行驶到 Y 位置 + service_time(Y)
    else:
        # 正常任务节点
        ...
```

### transshipments 格式

```python
# {(sat_id, beneficiary_idx, pick_node, new_phys):
#   (old_phys, carrier_idx, container_w, container_v)}
#
# beneficiary_idx: 取货车 V2 的路线索引
# pick_node: 被搬迁的 pickup 节点
# new_phys: V2 的新取货位置 (= 放容器位置 Y)
# old_phys: V2 的原始取货位置 (= 取容器位置 X)
# carrier_idx: 搬运车 V1 的路线索引
# container_w, container_v: 容器重量和体积
```

---

## 具体改动

### A. Model 3 精确求解

#### A1. model.py
- **删除**：`y_trans`, `dq_trans`, `dw_trans` 变量（第 189-206 行）
- **删除**：`vars_dict` 中对应项
- **保留**：所有其他变量不变

#### A2. constraints.py
- **删除**：`y_trans/dq_trans/dw_trans` 解包（第 37-39 行）
- **删除**：`S_TRANS` 解包（第 69 行）
- **删除**：横向中转约束区块（第 388-482 行）
- **简化**：E2 载重约束去掉 `net_q/net_v`（第 348-368 行），改为直接：
  ```python
  Q_r[j, v] <= Q_r[i, v] - q_j + M_weight_e2 * (1 - z[i, j, v])
  Q_r[j, v] >= Q_r[i, v] - q_j - M_weight_e2 * (1 - z[i, j, v])
  ```
- **简化**：E2 访问约束改为所有节点只访问 1 次（第 199-214 行）

#### A3. solver.py
- **删除**：中转诊断打印（第 131-141 行）
- **添加**：后处理搬运搜索（调用共享模块）
- **修改**：`solve_exact` 函数末尾加入后处理

### B. Model 3 ALNS

#### B1. alns_solver.py

**optimize_transshipment 重写**：
- 调用共享搬运搜索算法
- V2 保存：改变 task_to_phys[Cp_X]
- V1 插入：在 nodes 中插入 TRANS_PICK_X 和 TRANS_DROP_Y

**transshipment_destroy 重写**：
- 从 V1 路线移除 TRANS_PICK_X 和 TRANS_DROP_Y
- 恢复 V2 的 task_to_phys[Cp_X] = old_phys

**check_e2_route_feasible 修改**：
- 识别 TRANS_PICK_*/TRANS_DROP_* 虚拟节点的载量效果

**_print_solution 更新**：
- 打印搬运信息：V1 在 X 取、Y 放，V2 从 X 改 Y

### C. Model 4

#### C1. alns_solver.py

**_search_and_apply_relocation 重写**：
- 同 Model 3 ALNS 的 optimize_transshipment
- 调用共享搬运搜索算法
- 添加虚拟节点支持

**check_e2_route_feasible 修改**：
- 同 Model 3 ALNS，识别虚拟节点

**_build_transshipment_maps**：
- 删除（不再需要 trans_in/trans_out 追踪）

**postprocess_transshipment**：
- 不改接口，内部调用新的搜索算法

#### C2. solver.py
- 不需改（调用 alns_solver 的 postprocess_transshipment）

---

## 执行顺序

1. **创建 `utils/transport_search.py`**：共享的搬运搜索算法 + 虚拟节点工具函数
2. **Model 3 精确求解 MIP 简化**：删除接力中转变量/约束
3. **Model 3 精确求解后处理**：调用共享模块
4. **Model 3 ALNS 重写**：中转逻辑 + 虚拟节点支持
5. **Model 4 ALNS 重写**：中转逻辑 + 虚拟节点支持
6. **测试验证**

## 验证要点

1. MIP 去掉中转后能正常求解
2. 搬运搜索能找到 net_saving > 0 的机会
3. 搬运车插入停靠后可行性通过
4. 结果物理正确：容器从 X 搬到 Y
5. 最终距离 < 无中转版本
6. Model 3 和 Model 4 结果一致（相同的搬运逻辑）
