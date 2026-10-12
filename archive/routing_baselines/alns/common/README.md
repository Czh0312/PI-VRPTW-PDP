# 历史基线的 ALNS 共用组件

这里只服务上级目录的历史 M1–M4：`engine.py` 是搜索引擎，`types.py` 是路线／解数据类型，
`initial_solution.py` 构造初解，`fixed_repair.py` 修复固定分仓，`assignment_operators.py` 提供分仓算子，
`paired_operators.py` 处理配对送取，`route_exchange.py` 改进路线交换，`evaluation_cache.py` 缓存评价。
当前 M1–M8 使用 [models/routing/alns](../../../../models/routing/README.md)。
