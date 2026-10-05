# 隔离的历史算法基线

保留 M1–M4 的数学模型与 ALNS，以便核验历史功能并作为后续迁移素材。
`variants/` 是数学模型及精确后端，`alns/` 是对应启发式算法。

这些实现没有全部适配 `multi_batch_cutoff_v1`。旧 M3 包含历史静态与 single_day_backhaul_v1；旧 M4 是自由分仓的历史业务规则。不能与新版 M3 的 B=1 直接构成正式包装对照。

显式使用 `main.py route --baseline --case config/baselines/zhou_small_demo.json --model 1`；输出标记为历史基线并保存在 `output/baselines/`。
普通 `route` 和 `solve_routing` 默认拒绝历史策略。通用工具中的 single_day 分支仅服务本基线及回归。
