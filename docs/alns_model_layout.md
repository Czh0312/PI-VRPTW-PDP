# ALNS 的参数、状态、构造、邻域、评价和搜索控制

M1–M8 的 ALNS 各自独立维护以下职责。业务数据、时间规则、车队和成本仍使用
公共实例及成本模块；ALNS 不加载 Gurobi 或精确求解后端。

| 文件 | 职责 | 主要对象／函数 |
| --- | --- | --- |
| `parameters.py` | 搜索预算、温度系数、权重更新系数及更新周期 | 冻结数据类 `ALNSParameters` |
| `state.py` | 复制搜索状态、生成缓存键、把已有方案转换成搜索状态 | `clone_state`、`state_key`、`state_from_result`；M6–M8 另有 `clone_joint_state` |
| `construction.py` | 初解构造、分仓变更修复；M6–M8 的联合初始化与热启动转换 | `initial_state`、`chronological_state`、`appointment_state` 等 |
| `neighborhoods.py` | 破坏、修复、路线移动；联合模型的分批及分界移动 | `destroy_repair`、`repair_orders`、路线和日历操作函数 |
| `evaluator.py` | 完整解码、可行性评价、候选缓存；联合模型的成本评价 | `evaluate`、`EvaluationCache`；M6–M8 的 `JointEvaluator` |
| `search_control.py` | 加权算子选择、预算进度、温度、模拟退火接受与自适应权重更新 | `SearchControl` |
| `solver.py` 与搜索循环文件 | 输入校验，串联构造、邻域、评价和控制，维护最好方案并输出审计结果 | `solve_alns` |

## 各模型的搜索循环

| 模型 | 默认入口与搜索位置 |
| --- | --- |
| M1/M3 | `solver.py` → `search/single_batch.py` |
| M2 | `solver.py` → `search/assignment.py` → `search/single_batch.py`；另有分区搜索入口 |
| M4 | `solver.py` → `search/assignment.py` → `search/single_batch.py` |
| M5 | `solver.py` → `search/engine.py`；本模型 B=1 专门化使用 `search/single_batch.py` |
| M6 | `solver.py`；均匀周期由 `search/batch_search.py` 分配 K 搜索预算 |
| M7 | `solver.py` → `search/fixed_count.py`，并汇总 K 搜索结果 |
| M8 | `solver.py` → `search/calendar.py`；固定日历搜索在 `search/fixed_count.py` |

搜索循环主要展示以下过程：

```text
检查输入与必要条件 → 构造并评价初始状态
    → 选择算子 → 破坏／修复或其他邻域操作 → 完整评价
    → 审计并保留最好可行方案 → 判断是否接受当前候选 → 更新算子权重
    → 预算结束后审计并输出最好方案
```

不同模型保留各自的分仓、分批、日历和局部下降策略。M1–M5 的在线权重更新与
M6–M8 的分段更新仍分别使用原来的规则；温度下限、冷却形式、奖励值、重启、
局部改进预算和随机数消耗顺序没有因文件拆分而统一改写。

## 参数与搜索状态的区别

`ALNSParameters` 保存搜索的已知设置；当前路线、任务顺序和等待时刻则属于可改变的搜索状态。
`SearchControl` 接收当前搜索的开始时刻与时钟，并使用同一随机数生成器，协调接受和自适应更新。
原来的 `max_time`、`max_iterations`、`seed` 调用方式继续可用。

```python
from models.routing.model_3.alns.parameters import ALNSParameters
from models.routing.model_3.alns.construction import initial_state
from models.routing.model_3.alns.state import clone_state
from models.routing.model_3.alns.solver import solve_alns

# 正常求解入口，结果仍经过独立审计。
result = solve_alns(problem, max_time=10, max_iterations=5000, seed=42)
```

## 辅助模块与兼容入口

路线距离缓存、插入快速评分、数组化时间传播及局部下降继续使用本模型的专用模块。
这些模块用于减少重复计算或改进方案，完整评价和独立审计仍决定可行性与最终成本。

`evaluator.initial_state`、`neighborhoods.clone_state/state_key/state_from_result/EvaluationCache`、
原搜索模块的 `_move`，以及 M6–M8 求解器中原有的联合构造／评价／移动符号继续兼容旧调用。
新代码可直接从职责所属文件导入。M8 的`search.calendar.boundary_events/move_boundaries/initial_calendars`
也继续可用，实际实现分别归入邻域和构造文件。

每个模型只导入本编号的 ALNS 实现；没有跨模型算法公用目录。ALNS 输出可行方案及搜索诊断，
失败表示在给定预算内未找到方案，不产生最优性或不可行性证明。
