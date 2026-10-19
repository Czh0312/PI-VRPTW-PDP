# Gurobi 直接求解与定制精确求解

当前入口支持显式选择求解策略，八个模型仍拥有各自独立的数学模型和算法实现。
`solver='exact'` 表示算法家族，`strategy` 表示这一家族内的具体方法。
Gurobi 直接求解同样属于精确求解；使用启发式初解的完整算法也可以提供精确证明。

## 运行方式

```python
from models.routing.model_3.exact import solve_exact
from models.common.solver_config import MilpOptions

baseline = solve_exact(problem, time_limit=60, seed=42, strategy='milp',
                       milp_options=MilpOptions(threads=1, formulation='expanded'))
existing = solve_exact(problem, time_limit=60, seed=42, strategy='auto')
```

统一服务接口使用 `exact_strategy` 和 `milp_options`：

```python
from models.routing.service import solve_routing

result = solve_routing(model=3, method='exact', problem=problem,
                       exact_time=60, seed=42, exact_strategy='milp')
```

命令行可以直接保存两组结果。对正式数据替换 `--case`，两次使用相同算例、模型、预算和种子：

```bash
.venv/bin/python main.py route --model 3 --solver exact --exact-strategy milp --exact-time 60 --seed 42 --output output/m3_milp
.venv/bin/python main.py route --model 3 --solver exact --exact-strategy auto --exact-time 60 --seed 42 --output output/m3_default
```

`--milp-threads` 和 `--milp-formulation expanded|compact` 只用于 `milp` 策略。
也可在案例 JSON 中配置 `exact_strategy` 和 `milp_options`。
`--baseline` 仍指隔离的历史业务模型，不能用它代替当前模型的 Gurobi 对照。

## 策略与问题范围

| 模型 | `milp` | 默认 `auto` | 其他显式策略 |
| --- | --- | --- | --- |
| M1/M3 | 完整固定分仓 MILP | 原完整 MILP 流程，含确定性初解 | 无 |
| M2/M4 | 完整联合分仓与路线 MILP | 按规模选择联合 MILP 或分仓域树 | `joint`、`domain_tree`、`enumeration` |
| M5 | 完整多批次 MILP | 原初解、距离证书与完整 MILP 流程 | 原证书、改进和混合接口继续保留 |
| M6 | 固定联合实例直接求解；uniform 模式完整 K 域枚举 | 原固定联合求解或自适应 K 搜索 | `budget_strategy` 控制原搜索 |
| M7/M8 | 完整 K 域枚举，每个 K 直接求解联合成本 MILP | 原自适应 K 搜索与固定 K 求解 | `budget_strategy` 控制原搜索 |

M6–M8 的 `milp` **不是将所有 K 合并进一个 Gurobi 模型**。它按输入顺序单轮枚举全部允许 K，
未解决候选均分剩余预算，不启用外层解析下界、初解或重复访问。比较时应称为
“完整 K 域枚举 + Gurobi”与“自适应 K 搜索 + 定制子问题求解”。
本基线固定采用 `single_pass` 调度；默认路径的 `budget_strategy` 不改变它。

固定 K 的建模和求解接口用于诊断；其证明不能直接继承为完整问题证明。
预算结束时未访问的 K 仍留在 `count_search` 中，没有有效下界时保持未知。
全局下界继续按完整候选域取最小值，所有候选或有效支配证明闭合后才声明全局最优。

## 基线配置的确切含义

| 项目 | `milp` 配置 |
| --- | --- |
| 数学问题 | 保留原整数时间、车队、趟位、分仓、日历和成本口径 |
| Gurobi 内置功能 | Presolve、Cuts、Heuristics、MIPFocus 使用默认值 |
| 线程、停止差距 | 默认 1 线程；MIPGap 和 MIPGapAbs 为 0 |
| 外部初解和提示 | 不生成、不接受 MIP Start 或变量提示 |
| 自定义回调和证书 | 不调用容量割分离，不计算外部路线/库存/距离证书 |
| M1–M4 表达 | 默认展开的路线表达；可选等价压缩；不启用可选短环割、因果端点强化、仓流或路线标识界 |
| M5 表达 | 默认展开；关闭可选多批次载荷强化、容量割和距离证书行 |
| M6–M8 表达 | 默认展开；保留现有数学构建器中的必要箱池界及总成本界 |
| 预处理 | 保留必要域检查、安全时间/载荷界、稀疏弧和模型中的对称性处理 |

这是对**明确声明的完整建模表达**进行直接 Gurobi 求解，并非没有任何安全界或对称性处理的
“最原始裸模型”。必需的因果序位、库存和车辆连接约束不能作为加速选项关闭。
`expanded`/`compact` 只选择等价表达，不改变候选 K、可选仓或允许路线域。

`auto` 保留原有默认配置和按规模选择压缩表达的规则。基线和默认方法的比较反映整套方法的差异，
不能单凭该比较将提升归因于某一种割、初解或压缩方法；单项消融仍应使用对应的内部显式选项。

## 结果与时间口径

原有 `info.objective`、`best_bound`、`mip_gap`、`proven_optimal`、`proof_scope` 和 `audit` 保留。
增加以下元数据：

- `info.exact_strategy_requested`：用户请求的策略。
- `info.exact_strategy`：实际选中的方法，独立于业务日历的 `strategy` 字段。
- `info.solve_request`：本次总预算和随机种子。
- `info.solver_config`：直接 MILP 的线程、表达、启动/回调/证书开关等配置；
  原默认流程标记为 `existing_defaults` 并保留实际表达特征及逐 K 详情入口。
- `info.gurobi_best_bound`、`gurobi_version`：单个直接求解器的证据与运行环境。
- 完整 K 域结果的 `best_bound` 是全局汇总，不保留冒充全局界的单个 `gurobi_best_bound`；原始界在各次 visit 内。
- 完整 K 域的 `mip_runtime` 汇总全部已访问 K，模型规模字段仍只描述选中 K。
- `count_search_visits[*].solver_config`：各 K 实际分配的预算与配置。

预算用于预检查、建模和优化；提取、审计及结果规范化也计入总墙钟时间。
检查与清理必须完成，因此极短预算可能发生少量墙钟超出；不能把 Gurobi 的 `mip_runtime` 当成总时间。
命令行另存 `wall_time`。审计失败的基线不发布可行上界或最优性证明。
没有 incumbent 的 TimeLimit 不等于不可行，INF_OR_UNBD 也不等于已证明不可行。

对正式实验，应保存原始案例、费用配置、输入文件、源码版本和 Gurobi 环境；
使用相同预算、线程数和种子列表，包含超时和无可行解的运行。新结构不会自动保证性能提升。

本轮实现与检查结果见[重构验证记录](exact_refactor_validation.md)。
