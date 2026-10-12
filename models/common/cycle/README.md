# 共同周期机制

这里保存与求解方式无关的业务规则和数据，不是另一个编号模型，也不专属于 M3 或 M5。

- `instance.py`、`policy.py`、`state.py`：订单、固定日程、时间精度、包装差异和结果格式。
- `assignment.py`：分仓合法性校验、固定分仓实例转换和结果恢复，不包含搜索算法。
- `timing.py`：单批次固定路线的最早可行日程规范化。
- `audit.py`、`precheck.py`：独立事件审计、周期重放与必要条件。
- `scope.py`：编号入口的范围校验和结果来源标记。
- `migration.py`：显式重标并审计历史上误标 M3 的多批次周期结果。

精确求解在 `../../solvers/exact/`；ALNS 在 `../../solvers/alns/`。共享层不导入任一求解器或 Gurobi。
公开求解使用 `../../variants/model_1/`～`model_5/` 或 `../../service.py`。
M1–M4 只接收 B=1；M5 只接收 B>1。M5 的当前假设为固定分仓，尚未扩展多批次自由选仓。
