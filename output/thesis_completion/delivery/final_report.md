# 补充实验最终交付

2026-10-15。E0–E6 的实现、验证、登记实验和阶段分析已完成；最后的 E5 队列于 01:56（Asia/Shanghai）结束。交付范围是本算法仓库及可直接用于论文的表图、结论和局限。论文源码仓库未接入，按执行计划允许的替代交付完成材料，不声称已修改或编译论文。

## 实验与证据范围

[归档核验](integrity_review.json)覆盖 17 个研究阶段（含必要先导、参考恢复和候选对照）：1,491 次首次调用、1,492 个尝试，全部终态；1,429 个首次方案审计有效。其余记录包括已证不可行、启发式未解、建模/求解限时和一条基础设施错误，均原样保留。这里不把冒烟测试、未执行草案和历史实验混入正式计数，也不把种子重复当作独立现实样本。

核验逐一检查冻结 manifest、源码/输入和源码压缩包哈希，以及每个 completion 绑定的文件。有效首次方案的结果/审计哈希、目标值和连续双周期标志一致。E2/E4/E5 另存最终阶段重新独立计算的 final_audits；其他阶段的独立审计保存在每次原始 attempt。核验本身不重新调用求解器，也不构成任意输入上的正确性证明。

E1b 原门槛仍为 false：270 次首次调用中 269 有效，一次 Windows 状态文件替换错误无结果。唯一补跑仍使用冻结 worker，重新独立审计通过，另存恢复 review；主表不替换首次失败。旧正式队列 NEEDS_REVIEW 和新队列授权证据都保留。补跑并发与执行时间不同，仅用于恢复和敏感性说明。[逐尝试清单](attempt_inventory.csv)显式区分 primary 和 supplementary。

## 主要结论

| 阶段 | 完成内容 | 可支持的结论及限制 |
| --- | --- | --- |
| E0 | 216 次；186 有效、28 最优、15 不可行、15 启发式未解 | 已登记小机制例可复核；未知最优例只报告上下界，不把可行率当质量保证 |
| E1a / 精确先导 | 90 次 ALNS、18 次精确；45 个 ALNS 配对 8 改善、1 恶化、36 持平，精确均最优 | 六处预算回流/重访已验证；不声称每例改善或必用满名义预算 |
| E1c | 两版各 90 次；88 对最终费用相同，2 对开发例各 ±0.1；留出费用全部相同 | 采用首轮强制领箱必要下界，紧池首次可行约 9 秒降至 0.11–0.83 秒；按停止规则不再调参 |
| E1b | 270 次首次调用及一次补跑；三模型、三预算、五种子 | 900 对 60 秒费用平均改善约 6.31%/6.22%/5.81%；保留长预算恶化与一侧失败，无未知最优误差声明 |
| E2 | 正式 144 次均有效，23 最优；36 个匹配四元组 | 小例可分离决策作用；中大例 D 对 A 仅 9 改善、21 恶化，搜索尚未稳定实现更宽允许域的潜力，94 条条件比较逆序无证书冲突 |
| E3 | 正式 54 次：33 最优、3 限时可行、18 不可行 | Zhou 7 固定 K=2 的 M8 优势来自一级运输合并；共同 K 域下延后装货 M7 已达到同一最优值。一个 M8 案例仍有约 27.99% 界差 |
| E4 | 21 次有效：6 最优、15 ALNS 可行；六例阈值闭合 | 三小例重新安排作业各少一个箱；三中大例固定分批的同时领箱下界为 Q，不能给出连续缩池收益曲线 |
| E5 | 135 次有效，45 组三价格核对、全部重新双周期复核 | LaDe 观察到价格提高、池规模减少与作业增多的权衡；Zhou 两例池规模不变。90 个价格变化配对有 32 改善、34 恶化、24 持平，重优化并不保证胜过基准日程重计价 |
| E6 | 30 个当前精确最优参照，两版各 150 次有效 | 分仓访问预算候选使逐例平均最优误差 11.76%→8.51%，最好种子平均误差 9.46%→6.05%；65 改善、5 恶化、80 持平，平均种子标准差反而增加 |

分批/箱池联合决策确有物理权衡，但受限域的理论包含关系不能代替有限预算算法的实证表现。日历灵活性的价值需在共同 K 域和更强等长基线上评价，不能沿用旧的普遍优势或错误成本解释。价格响应需区分日程不变的会计重计价与重新搜索所得策略；本次合成价格不足以支持实际人民币收益或回本结论。

## 论文落点与证据索引

以下章节编号沿用计划，正式同步时按论文最新版核对。每份阶段报告都包含可直接引用的表述与适用条件。

| 论文内容 | 主报告 | 关键表图 |
| --- | --- | --- |
| 算法有效性、真实最优误差 | [E0](../e0_20261012_v1/findings.md) | [参照误差表](../e0_20261012_v1/tables/reference_comparison.csv)；该目录 figures |
| 6.2/6.7 预算与稳定性 | [E1a](../e1a_20261012_v1/analysis.md)、[E1b](../e1b_20261013_v1/findings.md) | [预算配对](../e1b_20261013_v1/tables/paired_budgets.csv)、[实例族](../e1b_20261013_v1/tables/budget_family_summary.csv)；费用轨迹、预算利用率图 |
| 困难结构与版本决定 | [E1c](../e1c_comparison_20261013_v1/decision.md) | [开发/留出](../e1c_comparison_20261013_v1/split_summary.csv)、[逐 K 诊断](../e1c_comparison_20261013_v1/per_k_diagnostics.csv) |
| 6.4 联合决策消融 | [E2](../e2_20261013_v1/findings.md) | [四元组上下界](../e2_20261013_v1/tables/ablation_contrasts.csv)、[来源组](../e2_20261013_v1/tables/ablation_family_summary.csv)、[反例](../e2_20261013_v1/tables/ablation_reversals.csv)；e2_components 图 |
| 6.5 日历机制 | [E3](../e3_20261013_v1/findings.md) | [固定 K](../e3_20261013_v1/tables/calendar_fixed_k.csv)、[共同域](../e3_20261013_v1/tables/calendar_best_over_registered_k.csv)；三策略时间线 |
| 6.4 箱池边界 | [E4](../e4_20261013_v1/findings.md) | [阈值](../e4_20261013_v1/tables/pool_thresholds_with_reference.csv)、[资源分量](../e4_20261013_v1/tables/pool_resource_summary.csv)；六例参考叠加/双周期图 |
| 6.6/6.8 合成价格响应 | [E5](../e5_20261013_v1/findings.md) | [逐配对](../e5_20261013_v1/tables/price_final_pairs.csv)、[均值/标准差/最好最差](../e5_20261013_v1/tables/price_instance_summary.csv)；e5_prices_00–02 |
| M2 独立算法补强 | [E6](../e6_comparison_20261013_v1/findings.md) | [最优误差](../e6_comparison_20261013_v1/optimal_error_statistics.csv)、[分仓诊断](../e6_comparison_20261013_v1/assignment_diagnostics.csv)；全 30 例误差图 |

## 验收边界与未解决的研究问题

- 全部结论限于各阶段冻结实例、K/时钟域、预算、成本与资源。来源嵌套、情景变体、种子相关性分别保留；没有把所有调用合并作独立显著性检验。
- E0 tight_pool_1、E2 tight_pool_1/D、E3 Zhou 10/M8 等有限预算界差仍未闭合。完成实验不等于证明每例最优；这些未决项已纳入阶段结论，未追加预算挑最好结果。
- E2 与 E5 的 ALNS 逆序、E1b 长预算恶化、E6 五个恶化配对和波动增加全部保留。算法仍有改进空间；后续算法实验应另行预登记。
- E4 大例的阈值证明来自固定分批同时领箱必要下界和同值可行见证；不是放开分批后的全局最小池，更不是费用最优证明。
- M2 双周期审计范围为车辆、服务及每周期新一次性包装，不作循环箱池结论。M6–M8 的库存、清洗释放和周期衔接按对应物理审计处理。
- 论文源码同步尚未执行：将上表插入对应章节，替换旧 M8 占用费/普遍优势说法；核对图表编号、引用、单位和条件；随后编译论文。当前交付已满足计划规定的“论文仓库不可用时先交报告与可引用表格”路径。

## 实现、验证与复现

主要实现提交：164676edf（六处预算调度），bbb1a6260（受限模型和诊断），a89d7a431（强制领箱下界），e84b7fc31（M2 分仓预算）；正式 E1b/E2/E4/E5 冻结求解版本为 4ed30cd11。E2 交付提交 3883143e1、E4 026c63dab；后续离线分析提交不改变已冻结求解源码。最终提交由本地 main 的交付提交记录确定，未推送。

运行前环境与真实整数求解预检记录保存在各阶段 environment.json 及 E1b infrastructure_recovery/preflight_and_validation.json。此前专项回归覆盖预算/跨 K 证明、独立实现边界、固定资源/日历、M2 分仓、驱动/恢复等；范围和计数见执行计划及各阶段报告，不将有交集选测累加称完整套件。本轮价格报告/匹配/机制选测 18 通过、0 失败、0 跳过（一个第三方弃用警告），显式 WithoutGurobi，未新增精确求解验证。

每阶段 manifest.json、environment.json、source_*.zip、cases/、inputs/ 和 runs/<job>/attempt_*/ 提供冻结源码、输入、命令及原始状态。跨阶段索引见 integrity_review.json 和 attempt_inventory.csv；失败与限时记录和成功记录具有同等可追溯性。阶段报告内给出离线复现命令；当前工作区分析工具比冻结驱动更新，应使用当前分析入口重建最终表图。

本机通过既有 tools/run.ps1 启动；以下是关键离线模块，均用 python -WithoutGurobi 模式传递参数，不运行求解器：

```text
-m experiments.analysis.verify_thesis_delivery
-m experiments.analysis.report_thesis_prices --study output/thesis_completion/e5_20261013_v1
-m experiments.analysis.analyze_thesis_mechanisms --study output/thesis_completion/e5_20261013_v1 --stage e5
-m experiments.analysis.report_thesis_pool --study output/thesis_completion/e4_20261013_v1
-m experiments.analysis.report_thesis_budgets --study output/thesis_completion/e1b_20261013_v1
```

verify_thesis_delivery 的 final_acceptance=false 表示它只自动判断归档完整性，不代替科学结论验收；最终逐项验收见 [acceptance.md](acceptance.md)。不修改历史阶段 gate 以制造全绿状态。全部工作仅在本地 main 保留，其他未提交文件保持原状。
