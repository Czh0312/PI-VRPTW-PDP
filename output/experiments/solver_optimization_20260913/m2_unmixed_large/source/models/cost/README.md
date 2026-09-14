# 成本模型

成本模型与 Model 1–7 解耦，只接收 `RouteMetrics`，其中包含订单量、E1/E2 里程和
车辆数量。公共接口由本目录 `__init__.py` 导出。

- `metrics.py`：路径结果到成本模型的稳定数据契约。
- `parameters.py`：经济参数结构。
- `lifecycle.py`：一次性包装、循环容器、运输成本及盈亏平衡计算。
- `analysis.py`：敏感性分析的公开函数。

运行与报告编排位于 `cli/cost.py`；成本参数位于 `config/cost/cases/`。
