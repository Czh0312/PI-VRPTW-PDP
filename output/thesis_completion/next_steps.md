# 接续执行记录（2026-10-13）

整体未完成。先读计划、README、最新 progress.json/stage_gate.json/pipeline.json，检查进程后再行动。仅本地 main 提交，不推送；保留无关用户文件；项目 Python 使用 tools/run.ps1，精确工作先预检。冻结文件不可改写，所有首次失败保留。

## 当前串行队列

1. pipeline_20261013_v1：E2 原输入精确诊断 6 次、E3 正式 54 次均已完成。
2. continuation_20261013_v1：共同截点恢复 6 次和 E1c 候选 90 次均完成，全部审计可行；候选决定见 e1c_comparison_20261013_v1/decision.md。
3. e6_pipeline_20261013_v1：15:18 完成 M2 候选 150 次，全部独立审计有效，阶段门槛 ready；E6 最终对照与图表见 e6_comparison_20261013_v1/findings.md。
4. formal_pipeline_20261013_v1：14:44 启动，父 PID 22368，15:19 自动开始 E1b；随后 E1b 270 → E2 144 → E4 21 → E5 135，共 570 次、调用预算和 56.5 小时。四阶段源码压缩包完全一致，冻结提交 4ed30cd11，冻结时源码工作区干净。先读实时 pipeline.json，不重复启动。

启动时父 PID 分别 22952、2068、25504，仅供定位，不能据此推断仍存活。前驱/互斥锁由驱动维护。每队列并发 2；不要另开求解负载。

## 结果出来后

- E3：54 次完成，机制分析、费用表、三张时间线和 findings.md 已生成并检查。33 最优、3 限时可行、18 不可行；有限 K 域有一个 M8 案例仍未闭合。原 Zhou 例固定 K=2 的 M8 收益来自一级运输合并，而在可选 K 域延后装货 M7 与 M8 最优值相同；论文应收窄解释。
- E1c 已完成：90 对均可行，88 对费用持平，M8 开发集一对 +0.1、一对 -0.1；紧池首次可行由约 9 秒变为 0.11–0.83 秒。采用首轮下界版，结束改进，不追加第二轮。已与 E0 精确参照核对域和输入：150 次有最优参照，30 次 tight_pool_1 只有可行上界；报告不混淆最优误差。
- 对比入口已实现：`tools/run.ps1 python -WithoutGurobi -Arguments @('-m','experiments.analysis.compare_thesis_candidates','--baseline','output/thesis_completion/e1c_20261012_v1','--candidate','output/thesis_completion/e1c_poolbound_20261013_v1','--output','output/thesis_completion/e1c_comparison_20261013_v1')`。已重建最终 90 对比较，complete=true；加 --reference-study output/thesis_completion/e0_20261012_v1 可重建 exact_reference_comparison.csv。统计专项选测 9 通过、0 失败、0 跳过；此轮显式 WithoutGurobi，未检查 Gurobi，不是精确求解器验证。
- E1b 正式版已冻结，共 270 次，60/300/900 秒、五种子、相同 K 域，排在 M2 队列之后。每次预算/种子匹配分析，保留未解与失败；不随中间结果改矩阵。
- 15:06 已补充 analyze_thesis_stages 的 paired_budgets.csv：按同实例/模型/版本/种子及消融组、固定池配对，核对冻结输入路径和 K 域，保留单侧失败及长预算恶化。用冻结 E1b 清单验证得到三种预算组合各 90 对，全部 NOT_RUN，属于分析管道验证而非实验结果。选测 tests/test_thesis_analysis.py 和 tests/test_thesis_candidate_comparison.py 共 14 通过、0 失败、0 跳过；显式 WithoutGurobi，未检查 Gurobi。运行完成后必须用当前工作区分析入口重建表（冻结求解源码不含此新增离线统计）。
- 15:42 离线分析修复：原 grouped_statistics 没有 group 字段，会将 E2 同例 A/B/C/D 合并；现按消融组和固定 N 分开，版本配对与轨迹也保留限制条件，消融差异按模型/版本隔离并拒绝重复组。E4 当前 case 名已含 N，原矩阵不会实际混池；新增 fixed_pool 键防止复用同名实例时混合，并在表中显式输出 N。选测 test_thesis_analysis、test_thesis_mechanism_analysis、test_thesis_candidate_comparison 共 21 通过、0 失败、0 跳过（一个第三方弃用警告）；WithoutGurobi，未检查 Gurobi。冻结清单集成检查输出 E2 48 个组统计（每组 12 例）、E4 9 个实例/池条件，全部 NOT_RUN，不能当作实验结果。运行源码/输入保持冻结；完成后用工作区当前分析工具重建表。
- E6 已完成：采用分仓预算候选，结束本轮，不额外调参重跑。两版共 300/300 审计有效，30/30 当前最优参照；平均最优误差 11.76% → 8.51%，65 对改善、5 对恶化、80 对持平。五种子最好误差逐例均值 9.46% → 6.05%，但种子标准差平均 1.75 → 2.73 个百分点。15 个原始实例族分别汇总；M2 双周期审计仅覆盖车辆/配送及每周期新一次性包装，不作循环箱池结论。最终 findings.md、CSV、PNG/PDF 已生成并检查。分析入口增加匹配资源/预算校验；统计选测 10 通过、0 失败、0 跳过（WithoutGurobi，未检查 Gurobi）。
- 14:16 接续检查：E1c 正常推进，两个队列父进程均存活，未重复启动。已为比较工具增加 per_k_diagnostics.csv，累计每个 K 的全部访问耗时及审计有效候选费用，未观测时留空而非记零；统计选测 10 通过、0 失败、0 跳过（WithoutGurobi，本轮未核验 Gurobi）。最新检查快照保存 heartbeat_checks.jsonl，候选全部完成后重建比较表再作版本决策。

## E2/E4 参考恢复及正式设计

16:01 离线 E2 证据边界补齐：ablation_contrasts.csv 现在同时记录每组审计上界/有效下界、最优标志、五个边际差与交互差的界区间，以及 incumbent_reversal 和 certificate_conflict。前者仅说明受限问题的可行解优于放松问题当前解，不能否定允许域包含；后者是放松问题下界超过受限可行上界或被证不可行，出现时必须调查并阻止相关结论验收。界缺失不以零代替，限时未解仍可保留有效下界。选测 test_thesis_analysis.py 与 test_thesis_mechanism_analysis.py 共 15 通过、0 失败、0 跳过（一个第三方弃用警告），WithoutGurobi，未检查 Gurobi。冻结 E2 集成检查得到 36 个四组配对，全部 NOT_RUN、界全部未知；这仅验证分析管道。未更改任何冻结源码或实验任务。

原 12 例参考只有 6 小例有效；6 中大例全部启发式未解。精确诊断另证明 Zhou 20 客户子集原约束不可行，其余 5 例限时未解。保留全部原失败，未被恢复结果替代。

e2_refcutoff_20261013_v1 已完成 6/6 审计可行。它仅选择共同截点，原订单、车辆、K=3 边界、固定分批、N=Q 均不变。生成器 select_references 核对恢复集合必须恰为原失败六例，并通过冻结输入哈希验证其余配置不变。十二个选定参考重新独立审计通过。

已生成 config/cases/thesis_completion/restricted_v2：E2 144 次、E4 首批 21 次；每组重新加载并核对单元素截点域，实际秒级截点完全保留。编码采用原 cutoff_bounds_minutes 入口，tick 前后各四分之一 tick 的分钟区间经 ceil/floor 恰得到同一个整数，不允许移动截点，也不修改原 policy。reference_evidence.json 同时保留原输入日历、实际冻结日历、原失败状态和参考来源。

restricted_v1 是本轮生成后发现日历证据字段命名不清晰的未登记草案；没有启动实验，以 restricted_v2 为后续入口。已依据 E1c 决定在 formal_20261013 生成正式设计并加入候选门槛，四阶段清单/源码/输入均冻结至各自 *_20261013_v1 目录。不要把这批生成配置当成正式实验已完成。

E4 三小例枚举必要下界至 Q 的全部整数。三个中大例的既定固定分批均只有一批含所有需求，故下界=Q，只剩每例 N=Q 的五种子；低于 Q 的粗点在 necessary_pool_exclusions.json 记录同时领箱必要条件证明，没有编造求解调用。这里无低于 Q 的待定整数边界；可行 N=Q 的证据核验后，资源阈值可由必要条件与上界见证共同确定。不能将单个 N 点画成连续资源权衡曲线。若追加其他分批案例，必须另行预登记并与此首批分开。

16:22 检查确认固定日程缩池参考已经生成：ordinary_0 原日程需 N=2、repeated_orders_0 需 N=3、tight_pool_0 需 N=2；三个中大例分别需 48/78/201。E4 图表/解释协议已保存 e4_20261013_v1/analysis_protocol.md，在正式结果产生前固定时间线选择规则（每例最小审计可行 N 处的最小有效种子，不按最优费用选图）。待结果完成后补固定日程重计费用比较和两周期箱流/清洗释放时间线，不把原日程不可行称为重优化不可行。本轮仅检查和报告准备，未运行测试/求解或修改冻结实验。

截点精确等价、四组受限域与匹配统计选测 14 通过、0 失败、0 跳过；运行前正常授权身份 Gurobi 11.0.3 整数预检成功。
## 其余交付

### 2026-10-14 05:22 优先接续修正

14:02 恢复准备：新增 experiments/runs/recover_thesis_infrastructure.py，加载原冻结驱动并仅替换控制器 JSON 写入函数，对 Windows 5/32/33 错误最多重试 8 次；worker 仍从冻结 driver.py 启动。必须全部首次调用完成、失败 row 哈希匹配、原 gate 恰仅该失败且无证书矛盾才补跑一次；结果独立核对配置、结果哈希和双周期审计，输出 infrastructure_recovery/review.json，原 stage_gate.json 与主统计不改。恢复单独运行，仅一个活跃调用，资源差异列为恢复敏感性分析的局限。选测 test_thesis_infrastructure_recovery.py 与 test_thesis_completion_driver.py 共 24 通过、0 失败、0 跳过（WithoutGurobi，未检查 Gurobi），尚未启动恢复调用。

E1b 172/270 终态中有一条基础设施 ERROR：Zhou 50 / M8 / 300 秒 / seed42 的 attempt_001 在 57.74 秒时因 live.json 替换 WinError 5 被控制器终止，无解或审计输出。详见 e1b_20261013_v1/infrastructure_incident_20261014.md；全部原始文件保留。当前队列继续完成其余首次尝试，不并行补跑。待 E1b 结束核对 NEEDS_REVIEW，保存首次失败门槛，再预检、追加同冻结配置 attempt_002 和恢复审计；原门槛只看首次尝试，需显式、有证据的基础设施复核才能继续 E2，不能手工简单改 ready=true。主统计保留首次失败，补跑仅单列恢复及敏感性分析。

此后禁止 Get-Content 读取运行中 live.json；用文件 LastWriteTimeUtc 及静态 launch.json/PID 检查活性。必须读取的 progress/pipeline JSON 可先点导入 tools/data/read_thesis_json_shared.ps1，再用 Read-ThesisJsonShared，读完即关闭句柄再解析。显式共享删除的替换验证仍失败（沙箱及正常用户均如此），所以该函数不是已验证的消冲突保证。未来恢复控制器应增加有界写入重试并独立登记，冻结 driver.py/source 不改。

E5 135 次 300 秒合成价格重优化已冻结并排在 E4 之后。必须区别固定日程重计价。

生成的表和图片之后仍需写出论文结论、反例、局限，核对独立双周期审计及证明范围。工作区内未找到论文源码仓库，最终单列待同步项，不伪称已改论文。全部验收完成后才停止 automation 自动接续。

### 2026-10-14 14:18 接续结果

E1b 首次 270/270 完成，269 FEASIBLE、1 ERROR；原 formal_pipeline_20261013_v1 于 14:05 NEEDS_REVIEW 停止，原 stage_gate 未修改。正常身份预检 Python 3.11.3 / Gurobi 11.0.3 / 整数目标 1 成功。首次启动恢复脚本的模块路径错误已记录 startup_failure.json（未产生求解 attempt）；改由 tools/run.ps1 python -c runpy 从根目录执行同一不可变快照后，attempt_002 于 14:15 完成且独立双周期审计通过。恢复登记/失败哈希/控制器快照/结果/复核在 infrastructure_recovery/。

新增 report_thesis_budgets.py，已重建首次尝试主表、配对/四实例族预算响应图和 findings.md；900 对 60 秒费用相对改善均值 M6/M7/M8 为 6.31%/6.22%/5.81%，保留长预算恶化及一侧失败；补跑的受影响种子配对单列 budget_sensitivity.csv，不替换主表。图已目视检查。24 项恢复/驱动选测通过、无失败/跳过；该组选测 WithoutGurobi，另行正常身份预检成功。未宣称完整测试套件。

复核原进程退出、恢复 ready 且 E2/E4/E5 所有登记前置门槛通过后，14:16 启动 formal_continuation_20261014_v1（父 PID 25904，仅为启动记录），顺序 E2 144 → E4 21 → E5 135，并发 2，预算和 25 小时，仍使用冻结 4ed30cd11 源码/输入。新队列 registration 绑定恢复 review 哈希；旧队列 NEEDS_REVIEW 保留。先读新队列 pipeline 和 E2 progress，勿重复启动；不要读取运行中 live.json 内容。完成每阶段后用当前分析工具生成匹配表、机制图/独立复核和论文结论；E4 按 analysis_protocol 选时间线，E5 区分重计价/重优化。全计划和自动接续继续，未完成前不停止。
