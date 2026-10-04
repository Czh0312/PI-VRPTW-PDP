# 多批次 M5 ALNS / hybrid：聊天窗口交接

交接时间：2026-10-02 21:26:56（北京时间，Asia/Shanghai）  
项目根目录：`/Users/chenzeheng/Desktop/2E-VRPTW-PDD`  
当前阶段：夜间优化及收尾已结束；自动续跑 PAUSED；库存主模型数值失配与稳定性目标未完成。  
本窗口范围：多批次 M5，重点是100顾客、5卫星站、3批次、full箱池155；不代表其他模型/规模的优化结果。

## 接手者先看什么

1. 本交接文件，先确认结果口径、未采用方案和停止状态。
2. [夜间最终报告](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/docs/model5_alns_overnight_20261002.md)：15个登记组、失败、参数、源码和证据入口。
3. [最终检查点](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/checkpoint.json) 与 [证据索引](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/evidence_index.json)：当前最好记录、原生运行、种子54未执行状态和封存路径。
4. 如接续库存界诊断，再读 [种子53较低主模型解核验](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/probes/public1500_03/counterexample_diagnostic.json)、[初解/割设置重放](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/closeout/20261002_0658/master_audit.json) 和 [三个最好记录主模型复核](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/closeout/20261002_0658/protected_master_audit.json)。

最重要结论：已有一次完整独立验证的真实原生求解达到5%以内，但尚不能承诺稳定达标。2.343665%的最好记录使用历史下界组合，不是单次冷启动结果；660秒从头达到5%仍未实现。交接本身不授权重新启动已结束的安排。

## 用户目标与有效约束

用户要求持续优化多批次M5的ALNS/hybrid，大规模GAP至少压到5%以内，并特别关心从头求解与稳定性。先前对“能稳定求出高质量解”的表述必须受当前证据约束：已有一次原生完整达标、一次保守达标，也有同配置失败，稳定性尚未证明。

夜间授权只覆盖北京时间2026-10-01晚至2026-10-02 08:00；07:00后不启动新大例。该窗口已关闭，续跑于10月2日07:11:29完成收尾并暂停，本次仅保存交接。

保持原始数据、订单、容量、箱池、批次、时窗、物理车队与周转、清洗和周期库存规则。不能用受限路线池目标或局部MIP GAP作为完整模型下界/最优证明。只有完整物理日程、完整域模型/证书与同系数复核通过，才提升最好记录。

实验须先登记并冻结源码、输入、初解/种子和预算，保留原始日志及负结果；同预算受控对照、不同随机初解、增加计算时间和正确性诊断分开解释。共享机器负载不能完全控制，不推断总体速度或成功率。大负载顺序执行，先检查活动作业，避免重复启动。

本窗口原授权禁止提交、推送、发布、给其他聊天/外部发送消息和创建子代理。保护共享目录其他聊天的修改。后续授权范围以用户新指令为准，不能由交接自行扩大。

## 当前效果与证据边界

GAP定义：`(UB-LB)/abs(UB)`。

| 口径 | UB | LB | GAP | 状态/计时 |
|---|---:|---:|---:|---|
| full155最好跨运行组合 | 3176.1324560422977 | 3101.69454878038 | **2.343665%** | 新可行上界加同系数历史界；通过完整日程与历史主模型投影 |
| 种子49共同新ALNS初解预算对照候选 | 3176.1324560422977 | 3075.492845646789 | **3.168621%** | 1500秒上限，含初始化实际1497.567秒；开发对照，非独立原生复跑 |
| 种子51真实公开入口从头求解 | 3204.2882786993478 | 3071.34354207181 | **4.148963%** | 完整独立复核通过，实际1227.293秒，1500秒上限 |
| 种子50同配置原生求解 | 3294.7041546096525 | 3071.3434936359736 | **6.779384%** | 未达标；实际1500.295秒，包含登记的末端审计0.5秒容差 |
| 种子53保守重新认证 | 3197.145034795688 | 3045.7926940795637 | **4.733984%** | 原条件库存界排除，只采用本次覆盖参考证书；实际1034.853秒 |
| tight箱池78历史记录 | 3376.205880 | 3313.840430 | **1.847205%** | 今晚未重跑；不与full池混用 |

第七轮历史组合为3198.992737/3101.694549、3.041526%，本夜组合记录改善至2.343665%。第七轮660秒原生结果21.649843%原样保留，不能与1500秒结果当作同预算收益；见 [第七轮报告](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/docs/model5_gap_round7_20261001.md)。

种子50和51的166文件源码及输入哈希相同，仅登记种子不同；实际ALNS初解和限时搜索轨迹不同。因此“50失败、51成功”不构成受控的算法速度比较或稳定性统计。不同预算配置的种子53也不能用来作跨种子的因果比较。

核心证据：[最好跨运行记录](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/probes/conditional01/best_certified.json)；[种子49完整域验证](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/probes/conditional01/validation.json)；[种子51完整验证](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/probes/public1500_02/validation.json)；[种子53保守证明](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/probes/public1500_03/validation_reference_only.json)。这些是独立原口径记录，不能合并成一个虚构的单次求解证书。

## 已完成与当前保留版本

| 工作 | 当前状态/范围 | 主要入口与验证 |
|---|---|---|
| 统一hybrid流程与预算参数 | 代码保留；所有阶段共用截止时间，完整可行审计后更新上界 | [multibatch_hybrid.py](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/solvers/exact/multibatch_hybrid.py) |
| 保留覆盖LP、列与完整域定价模型 | 可选 `adaptive_pricing_mode='resume'`；保留同一次调用内的LP基和模型，退出/异常时释放 | [multibatch_route_cg.py](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/solvers/exact/multibatch_route_cg.py)；resume01一组660秒对照改善但未达标 |
| 直接细化价格证据 | 可选实验逻辑保留，识别最后未完成的定价证据；负向试验未设为默认 | [multibatch_pricing_polish.py](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/solvers/exact/multibatch_pricing_polish.py) |
| 数量行对偶修复 | 可选 `count_dual_repair=True`；完整域不等式与小例核验通过 | [multibatch_count_dual_repair.py](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/solvers/exact/multibatch_count_dual_repair.py)；count01一次对照改善 |
| 容量/subset行对偶修复 | 可选 `capacity_dual_repair=True`；固定价格修复更强不等于完整限时流程更好 | [multibatch_capacity_dual_repair.py](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/solvers/exact/multibatch_capacity_dual_repair.py)；capacity01整体退步 |
| 快速负列发现 | 可选 `route_cover_batch_pricing=False`代码保留；大例退步 | 默认仍为True，fastcolumns01结果保留 |
| 独立验证和封存 | 15组登记：13组完整封存、1组保守参考证书封存、1组未执行 | [逐组证据索引](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/evidence_index.json) |

当前生产默认仍为 `adaptive_pricing_mode='column_generation'`、`subset_time_fraction=.25`、`count_dual_repair=False`、`capacity_dual_repair=False`、`route_cover_batch_pricing=True`。1500秒试验配置与默认配置分开，不能认为所有可选改动已经成为默认。

最新相关回归记录为213项通过，冻结快速加列候选源码下也通过213项。测试证明覆盖的正确性和兼容性，不等于大例质量/稳定性。日志：[冻结源码213项回归记录](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/probes/fastcolumns01/fast_columns_frozen_regression.log)。本次交接没有重跑测试或求解器。

`resume`不会跨退出的Python进程恢复LP或分支树；`checkpoint.json`保存证据与作业管理状态，不是一个可复活的Gurobi内存会话。

## 尝试过但未采用为默认的方案

| 登记组 | 比较范围 | 已认证GAP或结果 | 最终决定 |
|---|---|---|---|
| budget01 | 暖固定初解，各320秒覆盖 | .25 → 0预留：24.555262% → 22.470112% | 局部正向，不能称冷启动改善 |
| fresh01 | 同新初解，各660秒 | .25 → 0预留：19.119531% → 20.813846% | 完整流程退步，默认.25保留 |
| dual01 / dual02 | 同暖状态，各180秒 | 重建15.172254% / 14.342874%，固定价格均22.329822% | 固定价格不能替代更新覆盖LP，未采用为默认 |
| resume01 | 同新初解，各660秒 | 重建23.461088% → 续算20.239661% | 保留可选续算，不证明稳定或5% |
| resume02 | 同新初解，各660秒，同时改3项预算 | 24.617900% → 19.814323% | 联合配置开发结果，不能归因单项；仍未达标 |
| count01 | 同新初解，各660秒 | 20.266310% → 16.673145% | 一组正向，开关默认仍False |
| capacity01 | 同新初解，各660秒 | 14.401832% → 17.349356% | 负向结果保留，开关默认仍False |
| long01 | 同新初解，各1500秒上限 | subset .25为4.315637%，0为4.912636% | 两组达标；加大预算不能算660秒算法收益 |
| conditional01 | 同新初解，各1500秒上限 | 条件105秒4.133222% → 305秒3.168621% | 开发对照正向，不能证明总体稳定性 |
| fastcolumns01 | 同新初解，各660秒 | 完整批量14.560234% → 提前负列26.471023% | 不改默认True；“先负列、再独立认证”尚未实现/测试 |

完整上下界、初解、计时、计划、失败日志和采用标准见主报告各节，不重复覆盖原目录。

resume02原冻结检查器曾因未建模零界叶子缺`best_witness`而失败，原日志保留。另登记的严格v2只处理零界/空列/未收敛`TIME_LIMIT_BUILD`分支，非零证书仍完整核验，最终封存通过。count01父进程曾因标准输入编码报错而未执行封存准备，补做封存而未重启求解。budget01初次2项测试因导入/mock隔离失败，修复测试隔离后通过。这些不是应直接重复启动的求解器作业。

## 库存主模型的失配：最高优先级未解决事项

种子53原报告raw主模型界3059.835590、最终GAP4.294761%。同一导出完整主模型在独立种子43下得到3045.793113，严格`IntFeasTol=FeasibilityTol=1e-9`、`DualReductions=0`重放仍如此；差约14.042477，不能当数值余量。

较低解的整数取整后变量界、全部线性行、所有激活指示约束违反均为0；固定全部坐标重放也得到3045.793113。因此较高库存界不接受。种子53在重建/导出、不同初解的诊断中可能再次报较高值，但提供已核验较低解作初解或禁用割平面时均返回较低值。结果提示割平面或搜索路径相关，这是诊断推断，具体数值机制/割尚未隔离，不能宣称已修复Gurobi或模型。

保守复核单独预登记，排除全部有问题的条件证书，用本次原始完整域覆盖下界3045.792694和完整物理方案得到4.733984%。它通过23项模型检查、6次完整周期审计、19037条列重放、182次对偶修复检查，但不是原报告全部通过。

原检查器/失败日志、求解JSON和断言未改写，256份文件保留在 [保守证明及原失败封存](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/probes/public1500_03/failure_artifact_manifest.json)。没有生成原门槛需要的`validation.json`和`VERIFIED final_verification.json`，所以种子54没有执行；不要通过伪造验证状态或去掉门槛强行启动。

新较低库存解只是库存松弛模型解，不是完整M5可行上界。支持种子51、种子49候选、历史full组合的三个主模型已在关闭割、严格容差、种子53的额外核查中复现原raw值3071.343849/3075.493153/3101.694859，记录维持有效。核查证据：[受保护主模型重放](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/closeout/20261002_0658/protected_master_audit.json)。

## 源码、输入、参数和运行现场

统一入口是 `models.routing.variants.model_5.exact.solve_hybrid`；核心流程在 `models/routing/solvers/exact/multibatch_hybrid.py`。库存诊断还涉及共享的 [multibatch_conditional_inventory.py](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/solvers/exact/multibatch_conditional_inventory.py) 和 [multibatch_e1_routes.py](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/models/routing/solvers/exact/multibatch_e1_routes.py)，不要覆盖其他聊天修改。

工作环境：项目`.venv/bin/python`，夜间Gurobi12.0.3，`PYTHONHASHSEED=0`。完整实例标识`zhou_Set2_100-5-1b_b3`，原数据/订单/车队/元数据冻结在各组`inputs`中，实际读取来源以各`plan.json`为准。例：[种子51完整冻结输入](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/probes/public1500_02/inputs/case.json)；[种子51计划与输入/源码SHA](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/probes/public1500_02/plan.json)。

| 配置 | 总上限 | ALNS | 初始上界重建 | 覆盖 | 条件 | subset预留 | 种子 |
|---|---:|---:|---:|---:|---:|---:|---|
| 原生覆盖预算较多 | 1500秒 | 15秒 | 180秒 | 1160秒 | 105秒 | .25默认 | 50 / 51 |
| 原生条件预算较多 | 1500秒 | 15秒 | 180秒 | 960秒 | 305秒 | 0 | 53 / 54（54未执行） |

共同选项：`target_gap=.05`、`adaptive_pricing_mode='resume'`、`count_dual_repair=True`、`capacity_dual_repair=True`、`extra_slots_per_vehicle=1`、`route_pool_max_columns=64`、`max_supply_conjunctions=1`、`max_conditions=3`、`max_subset_cuts_per_batch=6`；批量完整定价保持原策略。新增候选槽遵守原车队和最大趟次，并未增加物理车辆。开关组合的成绩不能归因于其中任一单项。

1500秒实际计时包含公开入口内部ALNS、模型释放及末端完整物理/周期审计；输入加载和独立证据复核另计。种子51/53未输入外部共同初解、历史解、价格或下界。共享初解对照为隔离变量而使用同一个新ALNS初解，并把初始化时间分别计入每组，口径仍与原生调用不同。

恢复入口：[最终检查点快照](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/closeout/20261002_0658/checkpoint_final.json)；[11文件最终源码快照SHA](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/rounds/20261002_0658_source/manifest.json)；[最终收尾哈希](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/closeout/20261002_0658/final_verification.json)。各大例仍有各自166文件冻结源码，不能直接用共享目录后续新增源码替代它们。

本次核对：11个拥有的生产源码/测试文件SHA与夜间检查点一致；最终收尾清单中12个本地文件和18个外部引用文件SHA保持一致。所有关键链接已检查存在。这是读取与哈希核对，未重新跑213项回归、定价oracle或完整求解验证。

活动状态：本窗口`running=None`、`tool_session_id=None`；本聊天夜间已无未完成作业记录，历史会话均已完成。自动任务`m5-alns`当前仍为PAUSED，配置文件为`/Users/chenzeheng/.codex/automations/m5-alns/automation.toml`，停止证据见 [暂停确认](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/closeout/20261002_0658/automation_closeout.json)。本次没有重新启用它，未做全机进程扫描。

共享的另一个精确M5聊天检查点为 [其他精确M5聊天检查点](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001/checkpoint.json)；本次只读核对其已结束、`active_process=None`。它的20/30/50顾客结果不能混入本100顾客实例。项目已有M1–M4交接索引和文件原样保留。

## 未解决的问题与建议第一步

后续需要用户新的工作指令；本交接只保存现场。

1. 首先读取种子53的原失败和较低解核查，在新的诊断输出目录最小化同一完整主模型的反例。明确区分松弛模型较低可行解与完整M5可行解，排查搜索路径/割/数值机制，不靠改断言容差掩盖差异。
2. 在求解预算内设计库存下界的独立核验与安全回退：未核验的高界不能影响最终认证GAP或目标达标判断；候选失败应回退到已独立认证的完整域覆盖下界。该生产防护尚未实现。
3. 修复通过必要小例和回归后，重新登记同输入/初解/总预算的控制组，再做相同配置多原生种子验证。种子50同配置的6.779384%是必须保留的失败基准；若维持其LB3071.343494，UB需降至3232.993151或更低才到5%，还需改善61.711003。
4. 单独推进660秒目标。持续定价、上界多初解与分配预算需受控比较；提前负列方案已经退步，不能凭小例或迭代次数声称性能收益。不要将1500秒的成绩归功为660秒改进。
5. 每轮先核对共享活动、冻结输入/参数/源码、顺序运行，完整独立物理与完整域证书复核后再提升记录。新的跨日安排不能沿用已结束的夜间授权自动创建。

## 不要重复或覆盖的内容

保留全部原始失败与封存目录、历史第七轮和更早证书。原验证脚本会重写日志，封存后不得直接在原目录重跑；若要复现，应新建目录并登记依赖。闭合周期审计、原物理车队和清洗库存不能简化来降低GAP。

种子54状态为`NOT_EXECUTED_PREDECESSOR_FAILURE_AND_07_CUTOFF`，没有成功或失败成绩。原驱动含固定2026-10-02 07:00截止和原前驱完整验证门槛，不能把这个未执行登记当作可直接恢复的活动作业。需要新任务范围、新目录、新登记，而不是绕过旧门槛。

本聊天自动任务`m5-alns`与其他精确聊天的`m5`是不同安排，不得因为接续本窗口去修改另一个安排。此交接不创建线程、不发消息、不重启求解、不提交/推送。

## 新窗口开场指令

```text
请先阅读 /Users/chenzeheng/Desktop/2E-VRPTW-PDD/project/20261002-212656-m5-multibatch-alns-handoff.md。
项目是 /Users/chenzeheng/Desktop/2E-VRPTW-PDD。
然后依次读 /Users/chenzeheng/Desktop/2E-VRPTW-PDD/docs/model5_alns_overnight_20261002.md、/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/checkpoint.json 和 /Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/model5_alns_overnight_20261001/evidence_index.json。
当前夜间优化已结束，m5-alns续跑PAUSED；本消息只授权阅读交接和核对现场，不授权恢复自动任务或启动求解器。
请复述原生4.148963%、共享新初解3.168621%、跨历史2.343665%和种子53保守4.733984%的证据边界，并说明库存主模型高界失配、稳定性与660秒目标尚未解决。
保持原数据、容量、批次、时窗、物理车辆/周转、清洗库存和封存证据，保护其他聊天修改；不提交、不推送、不发消息、不创建子代理。等待我的下一步具体工作指令后再接续。
```
