# 求解代码目录指南

八个模型分别保存在 `models/routing/model_1/` 至 `model_8/`。每个模型的
`exact/` 和 `alns/` 独立拥有自己的实现，包括加速组件；不跨模型调用求解算法。

## 从哪里开始阅读

正常调用只需找到对应模型的 `solver.py`。阅读数学定义时看精确算法的参数、变量、
约束与目标函数；阅读 ALNS 时看方案状态、构造、邻域、评价及搜索控制。
两类算法均按职责设置文件，不限制顶层数量。ALNS 主循环位于顶层 `search.py`。
直接 Gurobi 与定制方法的对照方式见[Gurobi 对照说明](exact_milp_comparison.md)。

```text
model_N/
├── exact/
│   ├── solver.py          # 公开求解入口
│   ├── model.py           # 公开建模入口，兼容现有调用
│   ├── formulation.py     # 实际数学模型组装
│   ├── milp_solver.py     # 直接 Gurobi 基线
│   ├── solution.py        # 路线和业务决策提取
│   ├── parameters.py     # 已知参数和安全派生界
│   ├── variables.py      # 决策变量
│   ├── constraints.py    # 业务约束
│   ├── objective.py      # 目标函数
│   ├── initialization.py # 精确求解的初解与热启动
│   ├── __init__.py       # 公共接口导出
│   ├── enhanced_solver.py # 完整增强 MILP 求解；M6–M8 为固定 K
│   ├── assignment_search/ # M2/M4 分仓域树和枚举
│   ├── k_search/          # M6–M8 完整 K 域调度与证明汇总
│   ├── preprocessing/     # M1–M5 安全界与域处理
│   ├── bounds/            # 下界、松弛与定价证书
│   ├── branch_cut/        # 完整 MILP 的有效不等式及割
│   ├── primal/            # 可行解构造与改进
│   ├── constraint_blocks/ # M5 按业务拆分约束
│   └── diagnostics/       # M5 单批次专门化与辅助组件
├── hybrid/                # M5 显式混合求解
└── alns/
    ├── solver.py         # 公开求解入口
    ├── search.py         # 主要 ALNS 迭代流程
    ├── parameters.py     # 搜索参数
    ├── state.py          # 当前方案的表示和复制
    ├── construction.py   # 初解与修复构造
    ├── neighborhoods.py  # 破坏、修复与其他邻域
    ├── evaluator.py      # 可行性和成本评价
    ├── search_control.py # 算子选择、接受和权重更新
    ├── __init__.py       # 公共接口导出
    ├── local_descent.py  # 局部下降（按模型需要）
    ├── polish.py         # 路线精修（按模型需要）
    ├── assignment/       # M2/M4 分仓搜索
    ├── k_search/         # M6–M8 跨批次数调度
    └── performance/      # 缓存、快速评分和时间传播
```

按模型实际需要设置子目录，不为没有相应算法的模型创建空壳目录。
完整模型组装统一在 `formulation.py`。下界组件与可行上界组件分别归入 `bounds/` 和 `primal/`；
用于松弛的割留在相应下界模块内，完整 MILP 的割放在 `branch_cut/`。
ALNS 的主循环、局部改进和日历移动放在顶层，分仓和跨 K 调度按职责分组。

## 如何调用

公共包入口和已有调用方式兼容；`solve_exact` 增加可选的 `strategy` 和 `milp_options`，
`exact/model.py` 的建模接口保持兼容：

```python
from models.routing.model_3.exact import solve_exact
from models.routing.model_3.alns import solve_alns

exact_result = solve_exact(problem, time_limit=60, seed=42)
alns_result = solve_alns(problem, max_time=10, max_iterations=5000, seed=42)
```

```bash
.venv/bin/python main.py route --model 3 --solver exact
```

M5 原有的证书、改进与显式混合实验接口仍由 `model_5.exact` 导出。
常规精确求解与 ALNS 保持独立，只有显式混合实验入口会调用本模型 ALNS。

## 当前 ALNS 整理

ALNS 采用 `solver.py` → `search.py` 的阅读主线；自由分仓在 `assignment/`，跨 K
调度在 `k_search/scheduler.py`。完整逐模型路径表见 [ALNS 模型结构](alns_model_layout.md)。
此次只整理职责和调用路径，不更改算法策略。M2 保持原分区搜索，M5 保留内部单批次
专门化，M8 保留固定日历搜索。每个模型的 `alns/README.md` 是具体文件导航。

## 精确求解的当前阅读顺序

1. `solver.py`：选择实验方法与配置。
2. `model.py` → `formulation.py`：读取参数、定义变量、添加完整业务约束和目标。
3. `milp_solver.py` 或 `enhanced_solver.py`：执行直接 Gurobi 或增强求解。
4. `solution.py`：提取方案；公共业务审计核验可行性和费用。
5. 自由分仓查看 `assignment_search/`；多 K 查看 `k_search/scheduler.py` 的完整域证明。

直接 Gurobi 与定制精确方法都属于精确求解。比较时使用同一实例、时间预算、线程数和审计口径，
记录实际方法及配置；完整问题证明、局部子问题界和受限路线池上界不能混用。

旧内部目录和转发文件已移除，仓库内调用、测试及实验入口已同步。每个 `exact/README.md`
提供真实文件导航。历史结果、归档基线和冻结源码保留原有路径；冻结源码诊断入口根据实际加载
版本选择路径，不把旧算法替换成当前算法。源码迁移会使相关旧证书缓存失效，需要重新验证或计算。

逐模型导航见[模型目录](../models/routing/README.md)。数学定义及 ALNS 职责分别见
[精确模型结构](exact_model_layout.md)和[ALNS 模型结构](alns_model_layout.md)。
验证结果见[重构验证记录](exact_refactor_validation.md)。
