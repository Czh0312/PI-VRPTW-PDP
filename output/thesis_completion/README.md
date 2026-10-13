# 论文补充实验

当前按 [执行计划](../../docs/thesis_experiment_completion_plan_20261012.md) 分阶段实施。整体计划尚未完成；最新接续条件见 [next_steps.md](next_steps.md)。

| 阶段 | 清单 / 报告 | 计划调用 | 已核对状态 |
| --- | --- | ---: | --- |
| 最终短预算验证 | [smoke_v3/report.md](smoke_v3/report.md) | 36 | 全部审计通过；11 最优、25 可行 |
| E0 原版参照 | [e0_20261012_v1/findings.md](e0_20261012_v1/findings.md) | 216 | 186 审计可行，28 最优，15 已证不可行，15 启发式未解；门槛通过 |
| E1a ALNS 预算策略 | [e1a_20261012_v1/analysis.md](e1a_20261012_v1/analysis.md) | 90 | 全部审计可行；45 对中 8 改善、1 恶化、36 持平 |
| 精确预算策略先导 | [e1exact_20261012_v1/analysis.md](e1exact_20261012_v1/analysis.md) | 18 | 全部最优，门槛通过 |
| E1c 困难结构诊断基线 | [e1c_20261012_v1/analysis.md](e1c_20261012_v1/analysis.md) | 90 | 全部审计可行；候选对照已完成，结论见下 |
| E3 三策略先导 | [e3_pilot_20261012_v1/mechanism_analysis.json](e3_pilot_20261012_v1/mechanism_analysis.json) | 9 | 8 最优、1 已证不可行 |
| E2 共同参考日程 | [e2_reference_20261012_v1/report.md](e2_reference_20261012_v1/report.md) | 12 | 6 小例最优；6 中大例原始失败保留，已另行完成参考恢复 |
| E2 原输入精确诊断 | [e2_refdiag_20261013_v1/report.md](e2_refdiag_20261013_v1/report.md) | 6 | 1 已证不可行、3 限时未解、2 建模限时；保留全部失败 |
| E6 当前精确参照 | [e6_reference_20261012_v1/report.md](e6_reference_20261012_v1/report.md) | 30 | 全部最优 |
| E6 原版 | [e6_analysis_20261012_v1/report.md](e6_analysis_20261012_v1/report.md) | 150 | 全部审计可行，部分实例仍有较大最优误差 |
| E3 正式三策略 | [e3_20261013_v1/findings.md](e3_20261013_v1/findings.md) | 54 | 全部完成：33 最优、3 限时可行、18 不可行；图表及有限 K 域机制分析完成 |
| E2 共同截点恢复 | [e2_refcutoff_20261013_v1/manifest.json](e2_refcutoff_20261013_v1/manifest.json) | 6 | 全部审计可行；原失败参考保留；四组配置共同冻结实际截点 |
| E1c 第一轮候选 | [e1c_comparison_20261013_v1/decision.md](e1c_comparison_20261013_v1/decision.md) | 90 | 全部审计可行；首次可行更早，最终费用基本持平；采用候选并结束改进 |
| E6 分仓预算候选 | [e6_comparison_20261013_v1/findings.md](e6_comparison_20261013_v1/findings.md) | 150 | 全部审计有效；平均最优误差 11.76% → 8.51%，65 改善/5 恶化/80 持平；E6 分析完成 |
| E1b 正式多预算 | [e1b_20261013_v1/manifest.json](e1b_20261013_v1/manifest.json) | 270 | 运行中；10-14 05:22 已有 172 终态、171 审计可行、1 状态文件写入错误，待补跑及复核 |
| E2 正式消融 | [e2_20261013_v1/manifest.json](e2_20261013_v1/manifest.json) | 144 | 已冻结，E1b 后运行 |
| E4 固定池重优化 | [e4_20261013_v1/manifest.json](e4_20261013_v1/manifest.json) | 21 | 已冻结，E2 后运行；必要条件排除点另存 restricted_v2 |
| E5 价格情景 | [e5_20261013_v1/manifest.json](e5_20261013_v1/manifest.json) | 135 | 已冻结，E4 后运行；仅为合成价格 |

E1b/E2/E4/E5 已统一冻结至源码提交 4ed30cd11，并加入完成 E1c 候选的阶段门槛。后继队列 formal_pipeline_20261013_v1 于 14:44 开始等待，15:19 已进入 E1b；570 次调用预算和 56.5 小时，当前正式结果仍不完整。`COMPLETED` 或门槛 ready 只说明登记调用已有终态且证据流程有效，不保证全部问题可行、最优或研究阶段验收完成。预算和不等于墙钟耗时。

三个 2026-10-12 队列均已完成；当前队列顺序为 `pipeline_20261013_v1` → `continuation_20261013_v1` → `e6_pipeline_20261013_v1` → `formal_pipeline_20261013_v1`。每个队列并发 2，精确每进程 1 线程；后继等待前驱完成，不重复启动。启动记录在各自 `launch.json`，实时状态在 `pipeline.json`。

原始结果、逐 K 诊断、费用轨迹和独立双周期审计位于 `runs/<id>/attempt_<n>/`。`summary.csv` 统计所有登记的首次尝试；重试另存 `attempts.csv`，不替换失败分母。源码版本、文件哈希、冻结输入、解释器和完整命令均在阶段 `manifest.json`。`source_*.zip` 可恢复源码；副本由 Git 忽略，恢复入口 `run_thesis_completion restore --output <阶段目录>` 不覆盖已修改文件。Git 中 progress/report 是提交时快照。

实现提交：`164676edf` 为六处预算调度修复，`bbb1a6260` 为后续受限模型和诊断，`a89d7a431` 为强制领箱下界，`e84b7fc31` 为 M2 分仓访问预算候选。冻结前运行正常授权用户的求解预检。最新选测分别为下界/跨 K/驱动 19 通过，预算回收/依赖隔离 75 通过，M2 优化/精确分仓/隔离 51 通过；均 0 失败、0 跳过，有范围交集，不累加称完整测试套件。早期验证范围见执行计划 9.3。

原 M2 30 例的历史输入哈希、当前物理输入及历史解重审均通过；重新建立当前求解器的 30 个精确参照，以避免直接继承重组前共享求解器的证明。保留全部 30 例及每例 5 种子；来源相同的情景变体不作为独立经验样本作显著性推断。

后续物理定义见 [受限模型说明](../../docs/thesis_extended_models.md)。`config/cases/thesis_completion/followup_v2/` 中含 draft 的设计尚待阶段决定。`followup_smoke_20261012_v1` 的 11 次验证全部审计通过，不代替正式结果。`followup_v1` 是失败的生成目录，`smoke_v2` 只冻结未执行，均不计入正式证据。

用户授权持续推进全部计划；工作保留在本地 main，不推送，保留其他未提交文件。同一聊天的自动接续仍有效，先核对进程和冻结证据；只在实质进展、失败或需要用户处理时通知。论文仓库当前未接入，先交付可直接引用的表、报告及局限，再单列待同步项。

