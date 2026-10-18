# M7 — 精确求解

本目录拥有独立实现，不导入其他模型的求解代码。相似文件不自动同步。

先按 `parameters.py` → `variables.py` → `constraints.py` → `objective.py` 阅读数学定义，
再看组装文件和 `solver.py`。参数类复用公共业务实例，变量类管理同一个 Gurobi 模型。
完整导航见[精确模型结构](../../../../docs/exact_model_layout.md)。

顶层只保留入口与核心定义；`search/` 管理搜索流程，`formulations/` 组装数学模型，`accelerators/` 收纳加速与辅助组件（有需要时才设置子目录）。
日常调用使用本目录的 `solve_exact`；内部模块路径不作为稳定公共接口。
统一阅读方法与迁移说明见[目录指南](../../../../docs/solver_directory_guide.md)。

## 入口与核心定义

| 文件 | 用途 |
| --- | --- |
| [constraints.py](constraints.py) | 按业务含义组织的约束组 |
| [initialization.py](initialization.py) | 独立重放物理可行初解、编码 MIP start，以及超时后的保留；无 ALNS 依赖 |
| [model.py](model.py) | 该编号的 MILP 建模入口 |
| [objective.py](objective.py) | 距离或完整成本目标函数 |
| [parameters.py](parameters.py) | 已知参数、安全派生界及参数数据类 |
| [solver.py](solver.py) | 本模型公开求解入口及编号校验 |
| [variables.py](variables.py) | 决策变量定义与 `ModelVariables` 分组管理 |

## 搜索流程：批次数、分仓与子问题

| 文件 | 用途 |
| --- | --- |
| [search/batch_search.py](search/batch_search.py) | 本模型的 K 搜索预算、结果与证明状态汇总 |
| [search/fixed_count.py](search/fixed_count.py) | 本模型固定 K 子问题的求解、搜索或结果提取 |

## 数学模型组装

| 文件 | 用途 |
| --- | --- |
| [formulations/fixed_count.py](formulations/fixed_count.py) | 本模型固定 K 联合成本 MILP 的组装 |

默认精确 K 调度为 `adaptive`：超过三个候选时，前 60% 总预算优先搜索三个最小 K，再按必要下界选择尚能改善上界的候选。全部 K 都保留在证明台账中；未建模候选只拥有已验证的解析下界，不被当成已经求解。箱池同时满足每批装载量、平均批需求和必然占用时段下界。总成本加入必要出入度、批次行程和箱池下界。

可通过 Python 的 `initial_result` 或命令行 `--initial-joint-result` 提供同模型、同输入与费用配置的物理可行方案。审计、建模、热启动和求解都计入本次时间；初解的生成时间需在混合实验中另行计入。超时保留上界，不继承输入方案的最优性证明。没有外部初解时生成均衡分批方案：展开形式保留部分 MIP start；压缩形式改用 `VarHintVal` 软提示，避免补全子问题耗尽短预算。完整且审计通过的物理初解始终按 MIP start 编码。

`__init__.py` 导出当前算法入口。基础数据与审计的依赖边界见[模型导航](../../README.md)。

二级客户弧按物理任务聚合：时间、载重与因果顺序只约束一次，逐行程内部弧为连续副本，其整数性由全局二元弧、二元访问和唯一覆盖保证。卫星出入弧保留逐行程状态。分批连接用直接蕴含消除 AND 辅助变量，纯回收行程仍不选择配送批次。

默认以任务数、车辆数和行程槽位估算二级内部弧副本数，超过 4,000 时启用压缩形式；小模型保留原展开形式。该阈值只选择等价表达和搜索方式，不改变候选 K 或可行域。`formulations/fixed_count.py` 中的 `build_model(..., compact_e2=True/False)` 可用于固定 K 子问题的等价性诊断。
