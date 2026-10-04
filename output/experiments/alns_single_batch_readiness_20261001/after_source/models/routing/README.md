# 路径层

当前编号模型按原有习惯放在 `variants/model_1/`～`model_5/`：
M1/M2 纯两级配送，M3/M4 单批次循环，M5 多批次循环；M2/M4 可自由选仓。
每个目录都有 `model.py`、`exact/`、`alns/` 和公共入口 `__init__.py`。

`service.py` 按编号调用对应目录，入口校验批次数和模型身份。
`solvers/exact/` 保存 MILP、解提取和自由选仓穷举；`solvers/alns/` 保存初解、评价和启发式搜索。
两种求解器分别按需加载，不互相导入；ALNS 不加载 Gurobi。
`common/cycle/` 只保存共同日程、订单、结果格式、分仓转换与独立审计，不包含求解算法。
`baselines/` 只保存显式调用的历史 M1–M4。旧 M5 中转与 M6–M8 没有恢复。
`alns/` 为历史基线的通用搜索组件；新版 ALNS 入口在各编号模型目录。

详见 [模型位置说明](../../docs/model_layout.md)。
