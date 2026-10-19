# ALNS 代码阅读与目录结构

本次整理的目标是方便阅读和定位代码。八个模型保持独立实现，公开 `solve_alns`
调用方式不变；算子、评分、接受准则、随机数调用顺序和预算策略保持原有逻辑。
业务实例、成本和独立审计继续使用公共模块，ALNS 不导入 Gurobi 或精确求解后端。

## 推荐阅读顺序

1. `solver.py`：确认本模型入口、输入校验和分派路径。
2. `search.py`：阅读主要 ALNS 迭代与最好解维护。
3. `state.py`、`construction.py`：理解解的表示、初解和热启动。
4. `neighborhoods.py`：理解候选方案如何产生。
5. `evaluator.py`：理解完整评价与可行性判断。
6. `search_control.py`、`parameters.py`：理解接受准则、温度、权重和预算。
7. 按需要阅读分仓、K 调度、日历移动及局部改进。

```text
model_N/alns/
├── solver.py              # 对外入口
├── search.py              # 主要 ALNS 循环
├── parameters.py          # 搜索设置
├── state.py               # 搜索状态
├── construction.py        # 初解与修复
├── neighborhoods.py       # 破坏、修复、路线／订单分批移动
├── evaluator.py           # 完整评价
├── search_control.py      # 算子选择、接受与权重
├── local_descent.py       # M1–M5 单批次局部下降
├── intensification.py     # M5 多批次强化
├── single_batch_search.py # M5 内部单批次专门化
├── batch_initialization.py # M6–M8 均衡分批初解
├── cutoff_moves.py        # M6–M8 批内分界移动与精修
├── polish.py              # M6–M8 路线精修
├── calendar_moves.py      # M8 日历边界移动
├── calendar_polish.py     # M8 日历联合精修
├── fixed_calendar_search.py # M8 固定日历搜索
├── assignment/            # M2/M4 自由分仓
├── k_search/              # M6–M8 跨 K 预算调度
│   └── scheduler.py
└── performance/           # 缓存、快速评分、时间传播
```

上图列出各模型可能拥有的文件，不要求每个模型全部创建；每个 Python 包保留 `__init__.py`。
顶层文件数量按实际职责决定，不限制为八个。

## 各模型的主流程

| 模型 | 搜索路径与职责 |
| --- | --- |
| M1/M3 | `solver.py` → `search.py`：单批次固定分仓的 ALNS |
| M2 | 入口经 `assignment/continuous_search.py` 分派，正式纯配送输入进入 `assignment/partitioned_search.py`，再调用 `search.py` 搜索路线 |
| M4 | `assignment/continuous_search.py` 创建分仓上下文，`search.py` 在持续路线搜索中提出迁仓 |
| M5 | `search.py` 为多批次主循环；内部 B=1 输入使用 `single_batch_search.py` |
| M6 | `solver.py` 负责校验与分派；`search.py` 搜索给定批次实例，`k_search/scheduler.py` 调度均匀周期的各 K |
| M7 | `k_search/scheduler.py` 调度各 K；`search.py` 优化订单分批、独立批内分界和路线 |
| M8 | `k_search/scheduler.py` 调度各 K；`search.py` 联合优化不等长日历、分批、批内分界和路线 |

M2 当前正式路径是分区搜索，保留已有多起点、迁仓、已有解复用和再次搜索行为。
此次不把它替换成连续迁仓策略，也不合并两套实现。

M8 的“固定日历”指批次起止边界固定，订单归属、批内分界和路线仍可优化。
`fixed_calendar_search.py` 用于 K=1 的正式搜索及小实例热启动。
`search.py` 的构造、状态、邻域和评价依赖直接来自定义模块，避免经另一搜索循环转导入。

## 搜索、快速评价和独立审计

主循环串联以下步骤，具体计算留在对应模块中：

```text
输入与必要条件检查 → 构造并评价初解
    → 选择算子 → 生成候选 → 完整评价
    → 审计并维护最好可行方案 → 接受或拒绝 → 更新权重
    → 预算结束后的精修及最终审计（保持各模型原有顺序）
```

`performance/` 收纳缓存、快速评分和时间传播。局部下降、强化及精修会改变搜索
候选与解，因此保留在顶层，不把它们仅描述为计算加速。

M1–M5 原有在线权重更新与 M6–M8 分段更新分别保留。冷却、奖励、重启、
局部改进预算、随机数消耗和各 K 的预算分配均未统一改写。
跨 K 搜索是启发式调度，访问全部 K 不能证明全局最优。预算内未找到解不等于
证明不可行；已有必要条件检查产生的明确证书及其适用范围继续保留。

## 调用和内部路径迁移

```python
from models.routing.model_3.alns import solve_alns

result = solve_alns(problem, max_time=10, max_iterations=5000, seed=42)
```

公开入口与参数保持不变。仓库内调用、测试和实验观察入口同步调整。
外部脚本直接引用内部模块时，应按下表迁移；旧 `search/` 包不与新 `search.py` 并存。

| 旧位置（均相对于本模型 `alns/`） | 新位置 |
| --- | --- |
| M1–M4 `search/single_batch.py` | `search.py` |
| M2/M4 `search/assignment.py` | `assignment/continuous_search.py` |
| M2/M4 `search/assignment_moves.py` | `assignment/moves.py` |
| M2 `search/partitioned_assignment.py` | `assignment/partitioned_search.py` |
| M5 `search/engine.py` | `search.py` |
| M5 `search/single_batch.py` | `single_batch_search.py` |
| M6 `solver.py` 中的主要循环 | `search.py`；校验与分派留在 `solver.py` |
| M7 `search/fixed_count.py` | `search.py` |
| M8 `search/calendar.py` | `search.py` |
| M8 `search/fixed_count.py` | `fixed_calendar_search.py` |
| M6–M8 `search/batch_search.py` | `k_search/scheduler.py` |
| `accelerators/cycle_cache.py`、`single_batch_cache.py` | `performance/` 下的同名文件 |
| `accelerators/single_batch_scoring.py`、`single_batch_timing.py` | `performance/scoring.py`、`performance/timing.py` |
| `accelerators/single_batch_descent.py` | `local_descent.py` |
| `accelerators/intensification.py`、`polish.py`、`calendar_polish.py`、`batch_initialization.py` | 顶层同名文件 |
| M6–M8 `neighborhoods.py` 中的批内分界函数 | `cutoff_moves.py`，原模块保留函数导出 |
| M8 `neighborhoods.py` 中的日历边界函数 | `calendar_moves.py`，原模块保留函数导出 |

历史结果、归档基线和冻结源码不改写。`run_thesis_completion.py` 的观察入口可识别
原始扁平目录、上一轮 `search/` 目录和当前结构。函数所属模块等内部路径元数据可随
迁移改变，结果审计、目标值含义和公开求解接口保持不变。

各模型的 `alns/README.md` 列出该模型实际拥有的文件及用途。
