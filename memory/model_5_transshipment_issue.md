---
name: model-5-transshipment-issue
description: Model 5 ALNS 中转过度累积问题分析及修复方案
metadata:
  type: project
---

# Model 5 ALNS 中转过度累积问题

## 发现日期: 2026-05-17

## 问题描述

Model 5（中转融入 ALNS 迭代）的结果**差于** Model 3（无中转），中转非但没有减少总距离，反而增加了。

### 实验数据 (C101 数据集, 100 客户, ALNS 5000 迭代)

| 模型 | 最优值 | 中转数 | 耗时 |
|------|--------|--------|------|
| Model 3 (无中转) | 2347.97 | 0 | 18s |
| Model 5 (中转融入ALNS) | 2488.89 | 45 | 44s |
| Model 6 (先优化再后处理中转) | 2343.64 | 2 | 32s |

Model 5 比 Model 3 差了约 141，比 Model 6 差了约 145。

## 根因分析

### 中转添加机制

`optimize_transshipment` 每次调用只找 **1 个最优中转方案** 并应用（net saving > 0 即执行）。

调用频率（5000 迭代中约 250+ 次）：
1. 初始化后调用 1 次
2. 每 20 次迭代调用 1 次 (`trans_interval = 20`)
3. 解有改进时额外调用 1 次

### 中转撤销机制

`transshipment_destroy` 是 4 个 destroy 算子之一，每次随机移除 1 个中转。通过轮盘赌与其他 destroy 算子竞争，实际调用频率远低于添加频率。

### 核心缺陷：贪心累积无全局视角

- 每次只评估**单个**中转的净收益（V2节省 - V1绕路），不感知同一搬运车上已有的其他中转
- 搬运车路线被 TRANS_PICK/TRANS_DROP 逐渐塞满，但计算新中转绕路时，基线已经是塞满的路线，"边际绕路"看起来很小
- 添加速度 >> 撤销速度，中转数累积到 45 个
- 单个中转看是正收益，整体效果是负的

### 之前修复的 bug（2026-05-17）

1. **初始解不可行**: E2 路线构建有 `len(routes) < max_v` 车辆上限，超限任务被强制插入不检查可行性。已改为和 Model 3 一样无限制构建。
2. **中转搜索卡死**: `optimize_transshipment` 穷举搜索 O(n^2) 太慢。已改为采样 + 解析绕路估算 + 2秒时间预算。
3. **绘图虚拟节点**: `plot.py` 无法识别 TRANS_PICK_X / TRANS_DROP_X。已加前缀解析。

## 修复方案

### 方案 A: 限制中转数量

在 `optimize_transshipment` 中限制每卫星最多 N 个中转（如 3 个），超过则不再添加。

**优点**: 改动小
**缺点**: 治标不治本，可能错过好的中转组合

### 方案 B: 改为后处理方式（推荐）

参考 Model 6，ALNS 主循环不涉及中转，求解完成后再做后处理搜索中转。Model 6 已验证此方式有效（2343.64 < 2347.97）。

**优点**: 中转不干扰主优化，效果已验证
**缺点**: Model 5 和 Model 6 差异变小

### 方案 C: 改进评估逻辑

每次评估新中转时，考虑同一搬运车上所有已有中转的累积代价。同时增加 `transshipment_destroy` 的调用频率。

**优点**: 保留 Model 5 的特色（迭代中搜索中转）
**缺点**: 实现复杂度高

## 关键文件

- `models/model_5/alns_solver.py` — Model 5 ALNS 求解器（中转融入迭代）
- `models/model_6/alns_solver.py` — Model 6 ALNS 求解器（两阶段，后处理中转）
- `models/model_3/alns_solver.py` — Model 3 ALNS 求解器（无中转，参考基线）
- `utils/plot.py` — 绘图（已修复虚拟节点映射）

## 相关记忆

- [[model_5_exact_relocation_plan]] — 中转从"接力"改为"搬运车取放"的修改计划
