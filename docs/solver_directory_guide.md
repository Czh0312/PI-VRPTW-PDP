# 求解代码目录指南

八个模型分别位于 `models/routing/model_1/` 至 `model_8/`。每个模型有直接 Gurobi 与 ALNS 两个入口。
精确算法研究组件已移到[归档](../archive/exact_research_20261019/README.md)，不再保留可执行的增强分支。

```text
model_N/
├── exact/
│   ├── solver.py          # 唯一公开求解方式：直接 Gurobi
│   ├── model.py           # 完整数学模型建造入口
│   ├── formulation.py     # 业务模型组装
│   ├── milp_solver.py     # 完整模型直接求解；M6–M8 为固定 K 诊断内核
│   ├── parameters.py     # 输入及模型变量界
│   ├── variables.py      # 决策变量
│   ├── constraints.py    # 业务约束
│   ├── objective.py      # 目标函数
│   ├── solution.py       # 方案提取与审计衔接
│   ├── __init__.py       # solve_exact 公共接口
│   ├── preprocessing/   # M1–M4：变量界、合法弧和分仓域
│   └── constraint_blocks/ # M5：分组业务约束
└── alns/
    ├── solver.py         # ALNS 入口
    ├── search.py         # 搜索主循环
    ├── state.py          # 方案表示
    ├── construction.py   # 初解和修复
    ├── neighborhoods.py  # 破坏修复邻域
    ├── evaluator.py      # 评价
    ├── search_control.py # 接受准则和权重更新
    ├── assignment/       # 按模型需要：分仓搜索
    ├── k_search/         # 按模型需要：ALNS 跨 K 搜索
    └── performance/      # 缓存和快速评价
```

各模型只建立自己所需的子目录。`models/common/cycle/unified_milp.py` 将 M6–M8 各自的完整业务块
装配为统一批次数模型，并只调用一次 Gurobi；`models/common/gurobi_disjunction.py` 负责线性装配。
它们不是分解算法，也不调用另一个编号模型的求解代码。

```python
from models.routing.model_3.exact import solve_exact
from models.routing.model_3.alns import solve_alns

exact_result = solve_exact(problem, time_limit=60, seed=42)
alns_result = solve_alns(problem, max_time=10, max_iterations=5000, seed=42)
```

`solve_exact` 只接受预算、种子、日志和直接 MILP 配置；`auto` 仅兼容旧配置，效果与 `milp` 相同。
旧增强策略或初解参数会被拒绝。ALNS 仍保留其自身参数与初解接口。

从 `model.py` 阅读数学模型，从 `solver.py` 阅读求解；M6–M8 默认返回完整 K 域模型，
显式 `batch_count=k` 只用于固定 K 诊断。结果包含实际方法、全局界、总时间和独立审计。

详见[模型结构](exact_model_layout.md)、[统一批次数 MILP](unified_batch_count_milp.md)、
[直接 Gurobi 配置](exact_milp_comparison.md)和[ALNS 结构](alns_model_layout.md)。
