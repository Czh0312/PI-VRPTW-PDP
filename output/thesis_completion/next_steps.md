# 接续执行记录（2026-10-13）

整体未完成。先读计划、README、最新 progress.json/stage_gate.json/pipeline.json，检查进程后再行动。仅本地 main 提交，不推送；保留无关用户文件；项目 Python 使用 tools/run.ps1，精确工作先预检。冻结文件不可改写，所有首次失败保留。

## 当前串行队列

1. pipeline_20261013_v1：E2 原输入精确诊断 6 次、E3 正式 54 次均已完成。
2. continuation_20261013_v1：共同截点参考恢复 6 次已全部审计可行；14:08 后运行 E1c poolbound 第一轮候选 90 次。
3. e6_pipeline_20261013_v1：等待第二队列；运行 M2 分仓预算候选 150 次。

启动时父 PID 分别 22952、2068、25504，仅供定位，不能据此推断仍存活。前驱/互斥锁由驱动维护。每队列并发 2；不要另开求解负载。

## 结果出来后

- E3：54 次完成，机制分析、费用表、三张时间线和 findings.md 已生成并检查。33 最优、3 限时可行、18 不可行；有限 K 域有一个 M8 案例仍未闭合。原 Zhou 例固定 K=2 的 M8 收益来自一级运输合并，而在可选 K 域延后装货 M7 与 M8 最优值相同；论文应收窄解释。
- E1c：用原 e1c_20261012_v1 对比 e1c_poolbound_20261013_v1，相同实例、模型、种子、预算、K 域匹配，先核对冻结物理输入一致。开发集和留出集分开，报告费用、可行率、首次可行时间、构造/评价与逐 K 时间。协议在原目录 round1_protocol.md。完成决定后再冻结 E1b/E2 正式版本；至多两轮有独立证据的改进。
- 对比入口已实现：`tools/run.ps1 python -WithoutGurobi -Arguments @('-m','experiments.analysis.compare_thesis_candidates','--baseline','output/thesis_completion/e1c_20261012_v1','--candidate','output/thesis_completion/e1c_poolbound_20261013_v1','--output','output/thesis_completion/e1c_comparison_20261013_v1')`。初次检查 90 对冻结输入一致，但候选尚未运行，comparison.json 明确 complete=false；完成后重建表，不引用这个空结果作结论。统计专项选测 9 通过、0 失败、0 跳过；此轮显式 WithoutGurobi，未检查 Gurobi，不是精确求解器验证。
- E1b：followup_v2/e1b_design_draft.json，共 270 次，60/300/900 秒、五种子、相同 K 域。冻结最终 M6–8 源码后排在现有队列之后，不能与诊断候选混为同版本。
- E6：analyze_thesis_m2 比较当前 30 个最优参照、150 原版及 150 候选；候选协议在 e6_analysis_20261012_v1/candidate_protocol.md。保留全 30 例和五种子，不要求候选必胜。

## E2/E4 参考恢复及正式设计

原 12 例参考只有 6 小例有效；6 中大例全部启发式未解。精确诊断另证明 Zhou 20 客户子集原约束不可行，其余 5 例限时未解。保留全部原失败，未被恢复结果替代。

e2_refcutoff_20261013_v1 已完成 6/6 审计可行。它仅选择共同截点，原订单、车辆、K=3 边界、固定分批、N=Q 均不变。生成器 select_references 核对恢复集合必须恰为原失败六例，并通过冻结输入哈希验证其余配置不变。十二个选定参考重新独立审计通过。

已生成 config/cases/thesis_completion/restricted_v2：E2 144 次、E4 首批 21 次；每组重新加载并核对单元素截点域，实际秒级截点完全保留。编码采用原 cutoff_bounds_minutes 入口，tick 前后各四分之一 tick 的分钟区间经 ceil/floor 恰得到同一个整数，不允许移动截点，也不修改原 policy。reference_evidence.json 同时保留原输入日历、实际冻结日历、原失败状态和参考来源。

restricted_v1 是本轮生成后发现日历证据字段命名不清晰的未登记草案；没有启动实验，以 restricted_v2 为后续入口。正式冻结前等待 E1c 结论、记录最终源码版本并将其候选阶段门槛加入 design.prerequisites。不要把这批生成配置当成正式实验已完成。

E4 三小例枚举必要下界至 Q 的全部整数。三个中大例的既定固定分批均只有一批含所有需求，故下界=Q，只剩每例 N=Q 的五种子；低于 Q 的粗点在 necessary_pool_exclusions.json 记录同时领箱必要条件证明，没有编造求解调用。这里无低于 Q 的待定整数边界；可行 N=Q 的证据核验后，资源阈值可由必要条件与上界见证共同确定。不能将单个 N 点画成连续资源权衡曲线。若追加其他分批案例，必须另行预登记并与此首批分开。

截点精确等价、四组受限域与匹配统计选测 14 通过、0 失败、0 跳过；运行前正常授权身份 Gurobi 11.0.3 整数预检成功。
## 其余交付

E5 prices_v1/e5_design.json 为 135 次 300 秒合成价格重优化，正式求解器版本未冻结。必须区别固定日程重计价。

生成的表和图片之后仍需写出论文结论、反例、局限，核对独立双周期审计及证明范围。工作区内未找到论文源码仓库，最终单列待同步项，不伪称已改论文。全部验收完成后才停止 automation 自动接续。
