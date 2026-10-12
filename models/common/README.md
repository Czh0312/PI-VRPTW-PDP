# 跨模型、跨算法共用规则

这里保存统一的数据、业务规则和独立审计，不保存求解或搜索算法。修改这些基础规则会影响所有使用它们的模型。
`__init__.py` 只声明包或导出公共数据类型。

| 文件 | 用途 |
| --- | --- |
| [data_loader.py](data_loader.py) | 读取网络、节点、订单和车队 |
| [matrix_loader.py](matrix_loader.py) | 读取带节点编号的距离／时间矩阵 |
| [operating_policy.py](operating_policy.py) | 选择并校验运行规则，包含历史策略的显式适配 |
| [solution_audit.py](solution_audit.py) | 历史 M1–M4 的独立路线审计 |
| [task_builder.py](task_builder.py) | 历史基线的一级、二级静态任务构造 |
| [domain/instance.py](domain/instance.py) | 网络实例和节点查询 |
| [domain/node.py](domain/node.py) | 节点数据结构 |
| [domain/vehicle.py](domain/vehicle.py) | 车队与容量数据结构 |
| [cycle/assignment.py](cycle/assignment.py) | 分仓实例转换、固定分仓投影及结果还原 |
| [cycle/audit.py](cycle/audit.py) | 完整周期、库存、路线的独立审计 |
| [cycle/availability.py](cycle/availability.py) | 订单可供货时间的必要条件 |
| [cycle/batch_counts.py](cycle/batch_counts.py) | 允许批次数 K 的合法域 |
| [cycle/calendar.py](cycle/calendar.py) | M7/M8 日历实例、决策还原和审计 |
| [cycle/instance.py](cycle/instance.py) | 固定批次实例及输入加载 |
| [cycle/joint.py](cycle/joint.py) | M6 联合分批实例、物理投影、成本结果与审计 |
| [cycle/migration.py](cycle/migration.py) | 旧 M3 结果迁移到现行 M5 的显式审计入口 |
| [cycle/policy.py](cycle/policy.py) | 批次、截止、清洗及运行参数的业务定义 |
| [cycle/precheck.py](cycle/precheck.py) | 求解前必要条件和保守行驶时间下界 |
| [cycle/scope.py](cycle/scope.py) | 统一校验编号／批次范围，填充模型结果身份 |
| [cycle/state.py](cycle/state.py) | 公共结果格式和空结果 |
| [cycle/timing.py](cycle/timing.py) | 统一结果中的时间表 |
| [cycle/uniform.py](cycle/uniform.py) | M6 等长共同分界实例、决策还原和审计 |
