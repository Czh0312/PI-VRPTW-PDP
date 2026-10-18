# M6 — ALNS

本目录拥有独立实现，不导入其他模型的求解代码。相似文件不自动同步。

按 `parameters.py` → `state.py` → `construction.py` → `neighborhoods.py` → `evaluator.py` → `search_control.py` 阅读算法职责，再看求解入口与搜索循环。
完整说明见[ALNS 模型结构](../../../../docs/alns_model_layout.md)。

顶层只保留入口与核心定义；`search/` 管理搜索流程，`accelerators/` 收纳加速与辅助组件（有需要时才设置子目录）。
日常调用使用本目录的 `solve_alns`；内部模块路径不作为稳定公共接口。
统一阅读方法与迁移说明见[目录指南](../../../../docs/solver_directory_guide.md)。

## 入口与核心定义

| 文件 | 用途 |
| --- | --- |
| [construction.py](construction.py) | 初解、时间表构造、分仓修复及联合热启动 |
| [evaluator.py](evaluator.py) | 完整路线、时间和库存评价及候选缓存、联合成本评价 |
| [neighborhoods.py](neighborhoods.py) | 订单破坏／修复、路线移动及分批／分界邻域 |
| [parameters.py](parameters.py) | 搜索预算、温度与自适应更新参数类 |
| [search_control.py](search_control.py) | 算子选择、冷却、接受及权重更新 |
| [solver.py](solver.py) | 本模型公开求解入口及编号校验 |
| [state.py](state.py) | 搜索状态复制、缓存键与已有方案转换 |

## 搜索流程：批次数、分仓与子问题

| 文件 | 用途 |
| --- | --- |
| [search/batch_search.py](search/batch_search.py) | 本模型的 K 搜索预算、结果与证明状态汇总 |

## 加速与辅助组件

| 文件 | 用途 |
| --- | --- |
| [accelerators/batch_initialization.py](accelerators/batch_initialization.py) | 小实例的均衡合法分批束搜索初解 |
| [accelerators/polish.py](accelerators/polish.py) | 有限时的路线重分段、配送回收联合移动与截点改进 |
| [accelerators/single_batch_scoring.py](accelerators/single_batch_scoring.py) | 单批次插入候选的快速评分 |

16 个订单以内，在同一总预算内预留路线改进时间。重分段允许上一批回收与下一批配送合并，联合移动可同时改变 E1 配送及回收车辆，并重新优化回收截点。每个候选都经过完整时间、数量和周期库存重放；只接收真实总成本下降。算法不读取参考最优解，不产生全局最优证明。

`__init__.py` 导出当前算法入口。基础数据与审计的依赖边界见[模型导航](../../README.md)。
