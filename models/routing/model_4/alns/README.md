# M4 自由分仓单批循环的 ALNS

分仓移动嵌入同一条路线搜索轨迹。迁仓时一起修复配送和回收路线，并给新分仓使用独立的评价缓存。[分仓搜索](assignment/continuous_search.py)

模型入口调用 `search.py`，把本模型的构造、算子与评价组件传给[共用单批次策略](../../alns/single_batch.py)。

- 构造初解和修复热启动路线。[construction.py](construction.py)
- 生成破坏／修复和路线移动候选。[neighborhoods.py](neighborhoods.py)
- 解码完整周期并计算可行性与目标值。[evaluator.py](evaluator.py)
- 生成局部下降候选。[local_descent.py](local_descent.py)
- 路线插入评分、时间传播和评价缓存。[performance/](performance/)

[公开入口](solver.py) · [ALNS 框架](../../README.md)
