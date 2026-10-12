# 模型代码边界

现行结构是 `models/routing/model_1/` 至 `model_8/`，每个目录独立拥有 `alns/` 和 `exact/`。
逐文件用途见[模型导航](../models/routing/README.md)。

修改 M3 的 ALNS，就进入 `model_3/alns/`；修改 M3 的数学建模，就进入 `model_3/exact/`。
不存在求解算法公用目录，不跨模型导入；M7/M8 的固定 K 求解已复制为本模型内部实现。
相同组件是独立普通文件，后续修复是否同步到其他模型需要明确决定。

`catalog.py` 和 `service.py` 只负责登记与选择入口。`models/common/` 的数据／业务规则／独立审计、
`models/cost/` 的成本口径仍然统一，修改这些基础部分会影响使用它们的模型。
如果要改变某个模型的业务定义，还需明确对应的审计和成本口径，不仅修改求解器。

历史 M1–M4 在 `archive/routing_baselines/`，通过 `--baseline` 显式调用。
已有结果和冻结源码不随当前目录修改。`make test` 复用现有隔离测试，检查八个模型两种算法的实际调用，
禁止加载其他编号模型的算法，并检查 ALNS 不加载精确后端。
