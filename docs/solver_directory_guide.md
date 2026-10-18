# 求解代码目录指南

八个模型分别保存在 `models/routing/model_1/` 至 `model_8/`。每个模型的
`exact/` 和 `alns/` 独立拥有自己的实现，包括加速组件；不跨模型调用求解算法。

## 从哪里开始阅读

正常调用只需找到对应模型的 `solver.py`。阅读数学定义时看精确算法的参数、变量、
约束与目标函数；阅读 ALNS 时看方案状态、构造、邻域、评价及搜索控制。
每个算法目录顶层统一为 **8 个 Python 文件，包含 `__init__.py`**。

```text
model_N/
├── exact/
│   ├── solver.py          # 公开求解入口
│   ├── model.py           # 公开建模入口
│   ├── parameters.py     # 已知参数和安全派生界
│   ├── variables.py      # 决策变量
│   ├── constraints.py    # 业务约束
│   ├── objective.py      # 目标函数
│   ├── initialization.py # 精确求解的初解与热启动
│   ├── __init__.py       # 公共接口导出
│   ├── search/           # 分仓、批次数、固定 K 子问题等搜索流程
│   ├── formulations/     # 完整、单批次或固定 K 数学模型的组装
│   └── accelerators/     # 安全界、割、定价、路线池及辅助改进
│       ├── single_batch/ # 单批次专用组件
│       └── multibatch/   # 多批次专用组件（M5）
└── alns/
    ├── solver.py         # 公开求解入口
    ├── parameters.py     # 搜索参数
    ├── state.py          # 当前方案的表示和复制
    ├── construction.py   # 初解与修复构造
    ├── neighborhoods.py  # 破坏、修复与其他邻域
    ├── evaluator.py      # 可行性和成本评价
    ├── search_control.py # 算子选择、接受和权重更新
    ├── __init__.py       # 公共接口导出
    ├── search/           # 分仓、批次数、日历等搜索流程
    └── accelerators/     # 缓存、快速评分、初解增强和局部改进
```

按模型实际需要设置子目录。例如 M6 的数学模型直接在 `exact/model.py` 组装，
其精确求解不需要额外的 `formulations/` 或 `accelerators/` 目录。
`search/` 管理搜索顺序和预算，`formulations/` 管理建模，`accelerators/` 管理辅助方法。
加速组件可能在主流程中默认启用，具体由调用方的条件决定。

## 如何调用

公共包入口、`solver.py` 的函数签名、`exact/model.py` 的建模接口和命令行保持原样：

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

## 内部路径迁移

内部模块随职责迁移，仓库内的调用、测试和实验入口已同步。外部脚本若直接导入内部模块，
需要按下表调整；不为每个旧内部路径额外保留转发文件。

| 原内部位置 | 新位置 |
| --- | --- |
| `exact/initial_solution.py` | `exact/initialization.py` |
| `exact/engine.py`、`assignment.py`、`assignment_tree.py` | `exact/search/` 下的同名文件 |
| `exact/formulation.py` | `exact/formulations/full.py` |
| `exact/single_batch_formulation.py` | `exact/formulations/single_batch.py` |
| `exact/joint_model.py` | `exact/formulations/fixed_count.py` |
| `exact/joint_solver.py` | `exact/search/fixed_count.py` |
| `exact/single_batch_*.py` 加速模块 | `exact/accelerators/single_batch/`，去掉文件名前缀 |
| `exact/multibatch_*.py` 加速模块 | `exact/accelerators/multibatch/`，去掉文件名前缀 |
| `exact/strengthening.py`、`domains.py`、`distance_bounds.py` 等 | `exact/accelerators/` 下的同名文件 |
| 两类算法的 `batch_search.py` | 各自的 `search/batch_search.py` |
| M7/M8 两类算法的 `calendar_search.py` | 合并进各自的 `solver.py`，保留 `solve_counts` 函数 |
| ALNS 的 `engine.py`、`single_batch.py`、分仓搜索文件 | `alns/search/` 下的同名文件 |
| `alns/joint_solver.py` | `alns/search/fixed_count.py` |
| M8 的 `alns/search.py` | `alns/search/calendar.py` |
| ALNS 的缓存、评分、时间传播、下降和 polish 文件 | `alns/accelerators/` 下的同名文件 |

历史结果、归档基线和冻结源码保留原有路径。支持冻结源码的诊断入口会根据所加载源码的
实际目录选择旧路径或新路径；证书缓存仍校验完整的相关源码指纹。

## 整理范围

| 模型 | 精确算法顶层：整理前 → 后 | ALNS 顶层：整理前 → 后 |
| --- | --- | --- |
| M1 | 28 → 8 | 14 → 8 |
| M2 | 30 → 8 | 17 → 8 |
| M3 | 33 → 8 | 14 → 8 |
| M4 | 34 → 8 | 16 → 8 |
| M5 | 65 → 8 | 16 → 8 |
| M6 | 9 → 8 | 12 → 8 |
| M7 | 12 → 8 | 14 → 8 |
| M8 | 12 → 8 | 16 → 8 |

数字包含 `__init__.py`，统计的是顶层文件。此次迁移 215 个模块（其中 5 个初解模块
只改名），合并 4 个转发文件；其余实现保留在对应职责子目录中。
求解目标、约束、预算策略、随机搜索和加速方法的启用条件没有调整。

验证包括全量回归（3,809 项通过）和追加的快照替换检查（1 项通过）。覆盖八个模型
的两类求解入口、全部内部模块的导入隔离、数学模型与独立审计、证书缓存失效，以及
新旧源码目录的实验观察入口。公共函数签名逐一比对一致。

逐模型文件用途见[模型导航](../models/routing/README.md)。更详细的数学定义和
ALNS 职责分别见[精确模型结构](exact_model_layout.md)和[ALNS 模型结构](alns_model_layout.md)。
