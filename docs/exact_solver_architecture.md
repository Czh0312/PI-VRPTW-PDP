# 精确求解范围

当前精确入口只保留直接 Gurobi。`strategy="milp"` 为默认，`auto` 是同一方法的兼容别名；
旧 `enhanced`、`joint`、`domain_tree`、`enumeration` 选项已移除，不能静默触发其他算法。

M1/M3 的固定分仓、M2/M4 的联合分仓、M5 的多批次业务规则均建立完整 MILP。
M6–M8 将 K、日历、箱池与路线纳入统一 MILP，一次 Gurobi 求解全域。
固定 K 建模接口仅用于诊断，不把其证明当作完整 K 域证明。

精确目录保留参数、变量、业务约束、目标、模型组装、直接求解和解提取。
M1–M4 的 `preprocessing/model_bounds.py` 与 `domains.py` 定义模型变量界和合法弧/分仓域，
没有搜索树、定价子问题、外部证书或自定义割。M5 的约束按业务拆分至 `constraint_blocks/`。
统一模型的选择变量与线性装配位于公共 `cycle/unified_milp.py`、`gurobi_disjunction.py`。

Gurobi 使用其内置预处理、割与启发式，不传入外部初解、变量提示或自定义回调。
只有完整模型的求解器最优状态与独立业务审计都通过，才报告最优；限时无解与不可行证明分开记录。
建模超时不求解残缺的候选域。ALNS 保持自己的初解、邻域与跨 K 搜索，且不导入精确求解模块。

数学范围与接口见[统一批次数 MILP](unified_batch_count_milp.md)、
[直接 Gurobi 说明](exact_milp_comparison.md)、[精确模型结构](exact_model_layout.md)。
旧算法和专用实验已从当前代码树移出，保存在[研究归档](../archive/exact_research_20261019/README.md)。
