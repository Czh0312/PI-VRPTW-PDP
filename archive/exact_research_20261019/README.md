# 已退役的定制精确求解算法

2026-10-19 根据研究范围调整，从当前源码移除了定制精确算法，仅保留直接 Gurobi 和 ALNS。
归档包 [source_before_cleanup.zip](source_before_cleanup.zip) 保存清理前的源码、测试、实验脚本及说明，
包含本会话刚完成的统一批次数 MILP；[manifest.json](manifest.json) 记录包与原文件 SHA-256。
[removed_paths.json](removed_paths.json) 记录从当前树移除的 294 个路径（含重命名的数学界文件）。

退役内容包括列生成/路线证书、逻辑 Benders、路线池与上界构造、初解安装、自定义割、
分仓域搜索、精确 K 调度、M5 混合求解，以及这些组件的专用测试与实验驱动。
混合测试中的业务审计、独立枚举参照和 ALNS 检查继续保留在当前 tests 中。
历史 output、数据和冻结实验没有改写。

该包用于独立目录中的历史研究复现，不作为当前 Python 包的导入路径。
如需复现，解压到单独工作目录，并使用对应实验的原输入及依赖；不覆盖当前源码。
早期实验已有专属冻结源码时，优先使用其原快照和哈希。

当前使用说明：[直接 Gurobi](../../docs/exact_milp_comparison.md)、
[统一批次数 MILP](../../docs/unified_batch_count_milp.md)。

清理验证：当前全套测试 `.venv/bin/python -m pytest -q` 为 **1132 passed**，
覆盖直接 Gurobi、统一批次数、独立业务审计、ALNS 与旧策略拒绝检查。
归档包与 822 个文件的 SHA-256 已逐项核验，294 个移除路径均可从包内恢复。
此验证不代表正式性能实验已重新运行。
