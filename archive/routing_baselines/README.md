# 历史 M1–M4 基线

这里保存旧业务策略下的 M1–M4，不与当前八个模型混放。

- `variants/model_1/`～`model_4/`：旧数学模型与精确求解。
- `alns/model_1.py`～`model_4.py`：旧 ALNS；`alns/common/` 是其共用引擎和算子。
- `common/`：旧商品流建模及精确辅助函数。

仍可显式运行 `main.py route --baseline --case config/experiments/baselines/zhou_small_demo.json --model 1`。
这些实现并未全部适配当前 `multi_batch_cutoff_v1`，旧编号结果不能直接当作现行模型的正式对照。
当前模型与文件用途见[路由目录](../../models/routing/README.md)。
