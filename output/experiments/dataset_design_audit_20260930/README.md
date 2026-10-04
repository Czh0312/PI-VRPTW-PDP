# 2026-09-30 数据设计审查

正式结果使用 `final/`，相同脚本的独立复算使用 `reproduction/`。
审查结论及解释边界见 [完整报告](../../../docs/dataset_design_review_20260930.md)。
复算与输入完整性记录见 [verification.json](verification.json)。

根目录的 CSV/JSON/图片是初次探索结果；`verified/` 为中间过程结果，
其中缩池审计曾因内存中的结果池配置快照未同步而报 `POLICY_MISMATCH`。
审查脚本已修正，正式 `final/` 与 `reproduction/` 中的 279 份缩池时间表均通过重放，
其中 277 份达到必要箱池下界。这只证明相应固定规则下的最小池，不证明路线最优。
