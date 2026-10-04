# 单批次 ALNS 夜间续跑

用户授权从 2026-10-01 夜间继续至次日早晨；本次按 Asia/Shanghai 2026-10-02 08:00 截止。当前聊天的 heartbeat 已创建，ID `m1-m4-alns`，每 30 分钟续跑，截止时间在调度规则中限定。

## 当前状态

第一轮描述性诊断已完成：重放上一轮终端尾段实验的 8 个已审计 M2 对照解，检查排名前 3 的完整顾客尾段候选，依次尝试实际的一层单站插入/两站交换、二层真实车辆重新排程。1350 次完整解码，268 个变体通过固定问题与自由分配问题的独立审计及周期重放；变体包含同一实例的重复结构，不能当作 268 个独立实例。没有超时，源码/输入/旧结果哈希均通过。

三个 50 顾客旧解均无合格尾段候选。五个 100 顾客旧解存在候选：索引 4 重新排程车辆使第一名候选通过；索引 6 原前三名都因 E2 时间窗失败，车辆重排后两项通过，最好减少 27.5369（该旧解的约 2.42%）。索引 5/7 的 E1 变体还能降低总距离，但大量完整评估约每例 0.5–0.7 秒；先把下一版聚焦于车辆排程，以隔离作用和控制开销。

首版设计保存在 `next_candidate_design.json`：中途完整审计的尾段热启动，比较保留序列、保留后重排车辆、原投影、投影后重排车辆四种表示，仍只接受严格的完整目标改善。00:15续跑已完成实现、正确性、关闭选项一致性和8对新质量实验，结果拒绝并恢复；详情见本文件最后一节和 `checkpoint.json`。原有质量门槛不放宽，额外要求至少一个长预算运行确实使用了重排车辆的审计热启动，此条件未达到。

源码和输入冻结到本目录，旧结果复制并保存 SHA。诊断每个问题最多 30 秒，超时如实记录。系统不允许 `ps` 读取进程，暂用系统负载记录干扰，不能保证没有其他聊天的实验。

生产 ALNS 的 15 个模块与本晚开始时快照逐字节一致；M2 分仓文件也与上一轮冻结恢复基线逐字节一致。正确 SHA 是登记中实际计算的 `4bcb6b06af34cb7dda3ad9747de84220865adc9b63a20535de5f2774e1adc7a3`；早先聊天压缩摘要写出的另一个 SHA 不准确，未以该文字摘要替代文件核验。脚本 SHA 在诊断完成后补记，标注捕获时间，不伪称诊断源码预登记。

## 保留约束

保护目前已采用的静态缓存与 lazy pickup prefix。M2 分组、尾段分配、中途审计热启动、末尾审计下降均拒绝并已恢复；详见 `docs/alns_single_batch_audited_route_descent_20261001.md` 及此前实验。下一版必须先预登记源码/输入/种子/预算/采用门槛，再执行计时质量实验。候选不可只靠局部几何或局部时序估计进入最终解。

仅编辑本聊天的模块/新文件，保留 M5 和精确求解等其他聊天修改。没有用户请求，不创建子代理。计时对照串行运行，不同时跑自己的测试/剖析。新鲜完整解码与独立审计保留全部业务约束。保留失败和负结果，拒绝则只恢复自己的改动。07:00 起收尾，08:00 截止后不启动新工作。

## 00:15 续跑：首个候选完整撤回并验证

M2四表示车辆热启动的40项检查、254项相关检查及关闭选项两组100顾客固定500一致性通过。登记8对、16次新的等时求解全部审计/周期重放有效。短预算均值−2.0313%，长预算+0.57946%，长预算0改善、0重排热启动，整体拒绝，未放宽门槛。实际仅两个短100用到重排热启动，一个短100用到保留序列，其余五次未用到；两长的中途检查及四个最终解均无合格尾段选项。

只恢复分仓文件，40项候选检查归档。完整回归2473通过14警告66.79秒；M1–M4八组24次固定500解全部逐值一致、审计有效；本版和此前11个实验档案核验通过。所有日志、结果、源码差异、登记SHA和失败决定保存在 `output/experiments/alns_single_batch_vehicle_seed_20261002/`，报告已更新。没有活动进程，也没有新增采用的生产代码。

下一轮改方向：先剖析M3的当前状态签名重复构造成本，若有足够占比，考虑在fixed solver中仅对未变更的current复用state_key，证明clone/accept/assignment context与noop路径正确，再登记两组100顾客CPU门槛（各≥2%，固定500结果精确一致）和质量对照。不要直接继续重跑零候选的尾段策略；如做新的联合分仓邻域，需要先验证E1+E2完整目标及候选覆盖率。

## 00:45续跑：第二版E1试探评分缓存拒绝，恢复验证中

状态签名剖析占比1.50%/2.01%，本轮不实施current-key复用。最初profile.py与标准库同名的启动失败日志原样归档，重命名后诊断输入/种子/预算未变。随后冻结M3的E1试探评分标量LRU缓存版本，36项新检查及124项相关检查通过；两个100顾客、固定500、seed231、各3次CPU配对全部精确一致与审计有效，CPU中位数减少2.47298%与3.54562%，通过各≥2%门槛。

8对等时M3质量（5-2/sc02，50/100，5秒seed232/233/234；10-1/sc02，100，15秒seed232/233）全部16次审计/周期重放有效，但短均值+0.74647%、长+4.27527%、短50+0.82285%、短100+0.67008%，长0改善，整体拒绝。未重跑/重抽样/子集采用。只恢复三个拥有模块，36项检查归档；目前完整回归2585 passed,14warnings86.64s，8组M1–M4的24次固定500复现仍在运行。共享目录新增检查导致总数变化，保留他人改动。

所有源码/输入/参数/CPU及质量原始日志/失败决定位于alns_single_batch_e1_trials_20261002。今晚仍无新增采用代码。下步先完成复现及全部档案核验，再诊断墙钟退火与搜索路径的关系；这仅是机制假设，不重新启用已失败缓存。

第二版恢复验证收尾：完整回归2585通过14警告86.64秒，M1–M4八组24次路线/分仓/目标/500迭代逐值一致，全部独立审计/周期重放有效。本版及此前12个档案核验、原登记SHA、恢复源码精确一致均通过；状态签名诊断冻结源码/输入SHA通过。自有验证进程均exit0，无活动自有进程。检查点ready_for_annealing_diagnostic，报告同步；不把CPU收益作为采用，下一轮另行设计诊断。

## 01:26续跑：退火隔离诊断通过，后半程E1回收策略计时中

预登记合成退火诊断：两个100顾客M3、seed241/242、固定500/max60，原基线与已失败E1缓存各用0.95/1.0/1.05合成时间进度，共24次串行。只改变温度进度，真实截止/末20%下降不变；全部500迭代、runtime<20秒、独立审计/周期重放有效。相同进度的两个源码逐值路线与接受轨迹完全一致，0.95与1.0控制也完全一致。1.05相对1.0在3/4组改变首次接受决策，均是同一E1pickup提议/状态/分数下温度变化；最终2/4组路线改变。这不能证明上一轮等时退步原因或新策略质量。原报告high_scale_exact含刻意改变的温度字段，trajectory_addendum排除该字段区分实际决策，原数据和报告保留。所有冻结源码/输入/登记/工作区拥有文件SHA通过，无生产改动。

另起新候选late_pickup：保留前半程原退火，进度>=0.5的M3 E1pickup上坡提议被拒绝，原退火计算及原随机抽样仍执行；其他算子和M1/M2/M4保留。15项新检查、84项相关检查通过，关闭选项两个100顾客、seed251固定500公开M3精确一致/审计有效。测试初次4失败是seed3夹具不可行，保留原日志并检查拒绝结果；输入场景命名/绝对pool_config路径的设置断言失败均在登记和计时之前，原记录保留，未删求解结果。

质量前冻结新源码/自闭合输入/脚本/预算/种子/门槛：5-3/sc02物理50/100、5秒seed252/253/254六对；10-2/sc02、100、15秒seed252/253两对。原四分层均值<=0%、单次<=5%、短>=3赢长>=1赢、全部16审计有效不变；额外要求短/长各至少一次真正否决原退火会接受的上坡回收。计时串行，结果待判断。仅拥有single_batch.py和新增测试，失败仅恢复这两项。日志/进程ID见checkpoint.json。

第三版收尾：8对全部通过质量与机制门槛，短均−2.6160%、长−0.7007%、短50−0.1268%、短100−5.1053%；短5赢1退（最大+2.2726%），长2赢。全部16新鲜审计/重放有效。实际否决5/8次（3短2长），合计短17、长61；没有否决的3次差异不能归因于该动作。2661项完整回归通过14警告77.26秒；八组24次M1–M4全审计有效，候选/工作区精确一致，M1/M2/M4六组也等同旧基线。启用M3刻意改变策略，关闭M3一致性已在质量前证明。

本版及此前13个档案冻结源码/输入、归档测试、无交叠拥有文件核验通过；single_batch.py的有意新策略单独登记SHA，其原版本保存在本版before_source。首次收尾核验误用route_recombine初始清单，原档案核验器实际使用input_hashes_completed；五个额外文件已登记，原文件无漂移。错误脚本/日志/JSON归档后改用原档案权威清单，无质量门槛或结果更改。诊断/输入设置/第一次测试失败和文档写入编码失败均保留记录，不删除失败证据。

据8对登记矩阵保留第三版15项检查和单模块改动，状态late_pickup_adopted_awaiting_longer_confirmation，无活动自有进程。这只是5/15秒有限样本，尚不能称稳定大规模改善。下一轮先登记更长预算独立确认，冻结原规则对照和新的种子/未改业务输入，预先约定确认失败仅撤回此试点；不要重新启用已失败E1缓存。详见checkpoint及夜间报告。

## 02:00续跑：30/60秒独立确认已登记并串行运行

没有活动自有进程，试点两个拥有文件SHA仍相同。新对照/候选冻结当前上下文，仅single_batch.py不同，对照使用原试点前的确切字节。30秒10-1原始日历、物理50/100、seed262/263/264六对；60秒5-2原始日历、100、seed262/263两对，均为已有档案业务输入，不新改窗口/容量/车辆/容器。数据/订单/车队/元数据字节保留，路径闭合；8项输入读取、数量、必要条件和SHA检查通过。物理50顾客为100的坐标子集，其他字段差异不能解释成纯规模效果。来源曾用于其他算法，不称从未见过的几何留出集。

预登记时间02:06:41。四分层均值<=0%、单次<=5%、短>=3/6改善、长>=1/2、16次完整审计及重放有效、两预算各至少1次实际否决，全部保留。任何确认条件失败即撤回整个刚保留的试点，仅恢复拥有模块并归档15项检查；原5/15成功结果保留，另写覆盖决定，不编辑历史计时结果。此前必要条件导入模块写错在任何质量计时前失败，错误保存；关闭选项两组100顾客、seed261、固定500输出一致/审计通过，位于最终质量登记之前。质量正在串行执行，禁止同时启动自己的测试/剖析/其他重负载。进程和日志见checkpoint。

30/60确认收尾：8对、16次全部独立审计/重放有效，四分层和实际否决门槛通过。30秒均−0.7453%（3赢3退）、50−1.2693%、100−0.2213%；60秒均−0.6164%（1赢1退），最大单次+1.7939%。实际否决7/8次，短175、长37；60秒seed262否决0次但改善3.0268%，seed263否决37次却退步1.7939%。长均值不能归因于实际否决动作，不称为稳定/普遍提升，所有退步保留、不重跑、不改门槛。

按原登记保留有限试点，无生产/测试新改动。新输入八组M1–M4、24次固定500、seed261，候选/工作区精确一致，全审计/重放有效；六组非M3还与基线一致。保持原2661项回归对应拥有文件字节，未重复整仓回归；新增共享精确模块变化已记录在两源码相同上下文。确认及此前14个实验（原试点+13档案）的冻结源码/输入/登记/归档检查/拥有文件链核验通过。无活动自有进程，状态ready_for_joint_supply_vehicle_diagnostic。下一轮优先联合E1供货与E2线路/真实车辆诊断，先验证新鲜解码及审计；追加诊断不作为同预算采用。报告和检查点同步。

## 02:29续跑：联合修复完成，转向下降队列上限

冻结当前218源码上下文、15输入及上轮全部16历史解，16解新鲜无缓存解码/独立审计/周期重放有效，目标等于原解。登记的供货/E2线路/车辆联合诊断17,691试探，没有完整总距离改善；所有正负结果及完整审计保存。前64名几何候选合计1,024，786有容量违反、979有时间窗违反（可重叠）；无单解超时。该有界负结果不证明联合修复整体无效。

另登记扩展排序诊断，6,804几何候选，128局部筛选通过后全部完整解码/审计，24个可行降距候选均在256名后。10个100顾客历史解最好额外降距0.0146%～0.2839%，几何名次270～525；6个50顾客无改善。再登记原有完整下降流程256/1024队列对比，32队列全部耗尽，2,204提案完整解码/审计；256全部无改善，1024在同样10个控制解重现最好改善。追加计算结果不能算同预算质量或速度提升。

9项诊断正确性检查通过。两次队列设置失败（JSON列表哈希键、旧空目录）均发生在任何提案前，原脚本/日志/登记分别保留；最终版增加键检查和禁止覆盖已有提案的目录保护。三份最终登记、218源码/输入/历史解SHA、原始成功/失败文件数量、全部十个最好审计及生产拥有文件核验通过。生产代码与已有测试未改，没有活动自有进程。完整档案位于alns_single_batch_joint_supply_diagnostic_20261002，源码和日志不可覆盖。

下一轮新候选仅固定M3末段队列256→1024，当前已采纳晚期回收SHA7f43作为对照；必要正确性/关闭选项复现之后另行冻结登记50/100短30、100长60同预算质量，保持四分层均值<=0、单次<=5、短>=3/6赢长>=1/2赢、全16审计/重放有效，并要求两预算均实际出现>256的E2队列。任何失败只撤回新候选，保护latepickup15检查与lazy前缀。参数/输入建议见新档案next_candidate_design.json。本轮无新采用，不把小幅旧解下降称稳定提升或真实GAP。

## 03:00续跑：固定M3扩容候选已登记计时

冻结当前222个上下文文件，基线为已采纳晚期回收7f43，候选仅single_batch.py不同：固定M3末段下降队列256→1024；关闭选项256复现原候选，M1/M2/M4保持原队列，M5仍要求B>1走原泛化路径。记录单个E2队列的实际峰值，不能用跨迭代或含E1的生成总数证明扩容。原筛选、恢复、严格改善、全解码与完整审计均保留，14项新检查与相关检查共65项通过；两个100输入seed271固定500关闭选项逐值一致、审计/重放有效。

设置阶段三类失败保留：冻结脚本语法错误发生在执行前；M5被误建B1的初始检查1失败64通过，按原规则改成拒绝检查；输入元数据路径未冻结导致必要条件已通过但路径检查失败，随后复制原provenance字节并仅改路径。关闭选项检查在元数据闭合之前完成，元数据不参与解码/求解，数据/订单/车队均未变。最终8项路径/顾客数/必要条件检查通过，三个输入15文件及所有来源SHA冻结。

最终登记于北京时间03:07:52：scenario02 5-3原物理50/100、30秒seed272/273/274六对；scenario02 10-2、100、60秒seed272/273两对。四分层均值<=0、单次<=5、短>=3/6赢长>=1/2赢、全16独立审计/重放有效原门槛保留，另要求30/60各至少一次实际E2队列峰值>256。交替版本、独立进程串行，当前session55166；完整16次只跑一次，不重抽样、重跑或改门槛。期间不启动自己的测试/剖析/其他重负载；最终结果待完整矩阵判断。失败只恢复本候选单模块、归档14项新检查，保护latepickup15检查及lazy/static缓存。档案alns_single_batch_descent_queue_20261002。

扩容收尾：8对16次全部独立审计/周期重放有效，所有质量与机制门槛通过。30秒均−2.5015%（4赢2退），50−0.8359%、100−4.1671%；60秒均−1.6692%（2赢）。最大退步+1.3101%保留。实际E2峰值>256出现在7/8次（短5/6峰971，长2/2峰925）；短50seed273峰195退步0.3515%，该次差异不能归因于后256名候选。队列峰值不证明后续候选实际被接受，不作因果/提速外推。

保留扩容单模块和14项新检查，M3晚期回收15检查/lazy/static缓存仍在。完整回归2884通过14警告86.65秒；八组M1–M4的24次固定500基线/冻结候选/工作区逐值一致，全部审计/重放有效。新模块SHA248afd65f2fe71c20a5ff81400c0d844ccaef164701a50cf6995c2234dfefbea；原控制7f43完整保存在before_source。注册/源码/输入/拥有文件及此前15实验历史链、共同修复档案的冻结证据/闭合清单/全部20199原始路径保留核验通过。辅助编译写系统缓存被拒绝的记录保留，内存语法检查通过；质量输入/门槛/样本未变。

无活动自有进程。下一轮先冻结新的60/120秒、50/100确认，建议现有10-1物理50/100 seed282/283/284短60，5-2 100seed282/283长120；两个版本只差本扩容模块，基线为已采用晚期回收7f43，其他当前上下文相同。确认任何门槛失败只撤回扩容及14项检查，保留晚期回收15项和历史30/60结果。原四分层/单次/胜场/完整审计门槛及两预算峰值>256要求不变。计时20分钟本身可能接近单次推进上限，必须检查活动session、保存检查点并在下轮接续验证，不能重复启动。详见checkpoint及新档案next_candidate_design.json；仍只是有限试点，没有稳定/真实GAP结论。

## 03:29续跑：扩容60/120秒确认已冻结登记

没有活动自有进程，当前248afd65模块和两组29项新增检查字节无漂移。新冻结224个当前上下文文件，对照仅替换为确切7f43晚期回收模块，其余与候选相同；三来源15文件的业务字节不变、路径闭合，原容器配置相同。共享精确求解模块single_batch_pickup_bound.py漂移已在两版本相同上下文记录；未编辑他人文件。八项路径/规模/必要条件和关闭扩容两组100、seed281、固定500精确复现及独立审计/重放通过，未重复此前2884项整仓回归，因为本轮没有自身生产/测试改动。

最终登记北京时间03:33:11：10-1原物理50/100、60秒seed282/283/284六对；5-2原100、120秒seed282/283两对。四分层均值<=0、单次<=5、短>=3/6赢长>=1/2赢、全部16审计/重放有效、两预算实际单个E2队列峰值>256的门槛均不变。16次交替版本独立进程串行，session21111；计时本身20分钟，结果完整后才判定，不重跑、不重抽样、不改门槛。若超过单次推进边界，必须保存活动session并下轮续收尾，不再启动同一矩阵。任何确认条件失败，单独撤回队列扩容及14项检查，保留晚期回收15项/lazy/static及原30/60历史成功数据。本轮记录位于alns_single_batch_descent_queue_confirm_20261002。


### 03:53 queue confirmation withdrawn; restoration verification pending
All16 serial60/120 solves valid. Means short=-0.286678pct, short50=-0.838016pct, short100=+0.264660pct, long=-1.449851pct. Short100 failed immutable mean<=0 gate; queue14 checks archived and ownedmodule restored exact7f43, late15 preserved. Prior30/60 raw/historical decision preserved. Fresh restored regression session88374 running; poll existing session, then8groups24public and closure. No duplicate timing or candidate retries.

03:56 withdrawal closure PASSED: fresh restored full2914pass14warnings75.02s, public8groups24solves exact/fullaudit/periodic valid,17prior archives preserved. New raw/closure SHA verified; no active own process. Current7f43 and late15/lazy/static preserved. Whole queue expansion rejected; original30/60 historical success unchanged. Next candidate requires a new immutable freeze and50/100 short/long matrix.


## 04:00 load-screen candidate frozen/registered
Read latest progress and audited-route-descent report; previous queue rejected/verified, no active prior session. Frozen225sharedsource +15 unchanged scenario02 input files, old late7f43 baseline. Candidate only2ownmodules: fixedM3 necessary event-load screen before bounded256 geometry queue; new context-local512 cache. M1/M2/M4 original scope; full decoder/audit retained. This changes candidate selection, not equivalent CPU optimization.65targeted14new passed, two100seed291fixed500 disabled exact/audited and8path/count/preflight checks pass. Original pool config bytes equal.
Initial collection failure and6test-fixture failures archived separately before quality: qualified tests.routing import; predicate comparisons restricted to precedence-valid orders because decoder early TIMING_CYCLE masks capacity flags; public tiny fixture replaces infeasible directM2/M4fixture. No production changes from fixture repair.
Finalregistration04:09:01, scenario02 5-3physical50/100 short30 seeds292293294,10-2 100long60 seeds292293,8pairs16serialruns. Same fourstrata<=0%,single<=5%,short>=3wins,long>=1win,all16audit/replay, plus actual capacity rejection in both budgets. Failure restoreonly2modules/archive14loadtests, preservelate15/lazy/static and priorqueue withdrawal. Active73302 do not duplicate or run heavy tests/analysis.

04:22 load-screen finitepilot retained, closure PASSED.8pairs16quality allindependent/replay valid; fresh no-cache16distance equal/valid. Means short30=-1.021107pct,short50=-0.998090pct,short100=-1.044125pct,long60=-2.542115pct; wins4/6short2/2long,maximum+0.543716pct. Mechanism actualrejections8/8,shortchecks68289/reject49008,longchecks35808/reject26347; no causal/CPU claim. Fresh2942passed14warnings74.50s;8groups24public candidate/live exact/audited,6nonM3 baseline exact (M3 maydiffer).18priorarchives closure verified. Own2modules+14tests retained,late15/lazy/static preserved,oldqueue1024 stayswithdrawn. No activeownprocess. Next60/120newfreeze/registration as next_candidate_design; any failure withdrawwhole current trial withoutchanging gates/reseeding/history.


## 04:29 load-screen60/120confirmation registered
Read latest report/checkpoint/progress; no prior active session. Own2modules+late15/load14 unchanged, all225frozen source bytes identical priorcandidate context (no sharedsource drift). New frozen exactcontrol2modules frompilot before_source and unchanged candidate,15businessinput files from queueconfirm origins onlypath rewrites;poolconfig exact. Two100 fixed500 seed301 disabled exact/audited/replay and8path/count/precheck pass. Prior2942suite preserved, no freshsuite claimed.
Finalregistration04:31:59:10-1original physical50/100 short60 seeds302303304,5-2 original100long120seeds302303,8pairs16serialrunsonce. Same fourstrata<=0,single<=5,short>=3wins,long>=1win,all16fullaudit/periodic,actual loadreject>=1run bothbudgets. Qualitysession25293 do not duplicate or run heavy own work. Anyfailure withdrawwhole loadscreen trial2modules/archive14newtests, preserve late15/lazy/static/shared and original30/60history. Postprocessing saved/syntaxchecked, exact source/input/param/log/checkpoint preserved.

04:52 fullconfirmation failed and loadscreen withdrawn. All16 no-cache freshdecode distanceequal/fullaudit/periodic valid. Short60mean+1.921153pct,50+0.933824pct,100+2.908481pct,short0wins1equal5worse,max+5.486088pct;long120mean-1.174523pct1win1worse. Actualloadrejections8/8 meetsmechanism. RestoreONLY2modules to exact7f43/controlclass, archive14loadtests in confirmation; preserve late15/lazy/static/shared and old30/60history. Freshrestoredfullregression84563 running, do not duplicate. Next8groups24public/closure pending; retain recoverablecheckpoint at turn boundary.

04:55 bounded-turn handoff: fresh restored full2987passed14warnings75.46s exit0. Public8groups24existing19582running (likely complete by nextheartbeat), no duplicate; closure19archive/rawSHA stillpending. Own7f43/e1f98 restored, late15protected, load14archived. Full quality/fresh16results preserved. Afterclosure route-distance hotpath profile only; new equivalent optimization requires preregistered exactoutput/two100CPU>=2pct gates then full50/100short/long quality.

05:00 previous loadscreen withdrawal closure PASSED. Existing19582public exit0,8groups24baseline/frozen/live exact/audited.19priorarchives registration/source/input/ownedhistory chain valid, newraw+closureSHA saved. Fresh2987suite alreadypassed; not repeated. Current7f43/e1f98 andlate15 preserved. No active own process.

05:09 路线距离缓存诊断完成：冻结226个源码、15个原业务输入，两组100顾客M3、seed311、固定500迭代/max60串行剖析均完整审计及周期重放有效。task_distance累积剖析占比1.316448%/0.909041%，没有据此实现缓存或称CPU提速。保留一次扁平结果字段读取失败，没有重跑。原始7文件SHA保存。

05:22 回收后缀算术简化试验拒绝并完成恢复核验。另冻结相同控制及新seed321 CPU/seed322–324质量计划，唯一生产改动是single_batch_scoring.py中简单max的有序比较；lazy前缀扩展和通用评分保持。52项必要检查通过，含12项新逐位评分/前缀/边界检查；关闭两组100固定500的完整输出精确一致且完整审计有效。两来源各3对交替串行CPU计时共12次，所有路线/分仓/目标/500迭代逐值一致、完整审计/周期重放有效，但CPU中位数只减少1.093236%和1.845002%，均未达原2%门槛。没有重跑、放宽门槛或启动已登记50/100短30/长60质量矩阵。

只恢复本版拥有评分模块的原字节，将12项新检查归档；晚期回收15项、lazy/static和其他聊天修改保持。恢复版新完整回归2989passed14warnings71.71s，随后两组100顾客M1–M4八组24次控制/冻结恢复/工作区公开求解逐值一致且完整审计/重放有效。25项收尾条件通过；前驱49个收尾/62个原始结果SHA和剖析7文件SHA保持，沿用前驱已核验19个历史档案证据链，没有称新鲜重跑全部历史或2.2GB共同修复原始字节。收尾读取预检列表失败的原脚本/日志另存，修正读取方式后通过，求解结果/登记不变。本版56个原始证据及收尾SHA另存。

无活动自有进程；23635完整回归、40825公开复核均exit0，不重复启动。检查点和报告同步。下一步仅为未实施草案：在不变路线准备上下文中惰性保存未受插入影响的后缀边distance/travel，保留原max算术和lazy前缀，不重新启用本次算术改动；新目录先冻结/登记，正确性与两组100CPU各>=2%门槛后才允许50/100短/长质量矩阵及更长确认。07:00收尾，08:00停止本晚安排；保持所有M2尾段/扩容/容量筛选负结果关闭。

05:55 后缀边缓存试验完成并拒绝。先冻结227源码/15原输入和参数，再仅改评分模块；54必要检查通过，关闭两组100seed331固定500输出精确一致。三对交替CPU各组全部12输出/审计一致，但候选慢1.2083%/0.3215%，失败原2%门槛，无质量启动/重跑/放宽。评分模块恢复原14579009字节，14新检查归档，晚期回收15项/lazy/static/共享修改保持。新完整回归3006passed14warnings69.83s；四模型8组24公开复核全部精确一致/完整审计有效，25收尾条件通过。直接前驱47收尾/56原始SHA和旧剖析7文件SHA保持。83715/97530已exit0，无需重启。

另冻结相同227源码/15输入，seed341两组100M1-M4八次cProfile及八次静态签名LRU观察均固定500/完整审计/周期重放有效，32730/99216均exit0。AST类名和行号核对发现上轮报告把通用__call__15.72%/17.10%与回收后缀5.47%/5.73%归属写反；已修报告并另存erratum，原始数据字节不变。新通用评分自身占比M1约11%、M2约8%、M3约8.5%、M4约16%，仅带仪器诊断。静态路线4096签名观察命中M3为65.28%/60.06%、M4为52.82%/42.61%；每评分器独立，不能跨问题/分仓缓存。这些命中不证明CPU/质量收益，观察包装影响墙钟，不比较阶段目标值。

下一版alns_single_batch_static_route_metadata_20261002已冻结控制源码/输入/seed351四CPU组/seed352-354双模型16质量对及门槛，尚无生产编辑或测量。先按checkpoint适配复制的旧M3脚本（CPU按family+model分组/日志不覆盖、disabled4组、每模型原四分层质量门槛），再仅实现M3/M4上下文4096有序任务行/同序builtin sum/类型标志缓存。M1/M2缓存关闭，lazy前缀/后缀不改，不缓存动态就绪/惩罚/可行性。四CPU组各>=2%并24输出/审计一致后才启动双模型20分钟30/60矩阵；每模型原3/6短改善和1/2长改善均须通过，失败整体撤回，无子集采用；再做40分钟60/120确认。计时串行，07收尾08停止，不启动跨08矩阵。当前无活动自有进程，检查点/报告/原始与收尾SHA已保存。

06:14 静态线路缓存整体拒绝并验证撤回。66必要检查/disabled4/preflight16通过；24固定500输出/审计一致，但四组CPU减少-0.28334/+1.06155/+0.42877/-0.66352pct均未达2%，未启动质量、不重跑、不子集采用。只恢复评分14579009，26新测试归档，晚期15/lazy/static/共享修改保护。新完整回归3073passed14warnings72.91s、8公开组24逐值一致/完整审计有效、28收尾检查通过；原始/收尾SHA保存。90029/26712已exit0，不重启。

06:21 通用评分算术单独试验通过CPU，32次双模型质量正在运行。先冻结229源码/15原输入/newseed361 CPU4组、seed362-364质量16对及全部门槛，再只改SingleBatchRouteScorer通用方法：简单2参数max的有序比较保持tie/NaN方向和零加法/除法/求和顺序，初始3参数max不改。M3/M4用新helper，M1/M2原回退AST不变；整个lazy _PickupInsertions类AST不变，未重新启用拒绝的后缀算术/边/静态线路/其他算法。69检查（29新）及disabled4/preflight16通过。24固定500CPU全部路线/分仓/目标/迭代数逐值一致、完整独立审计及重放有效；四组中位CPU减少M3-5-3=2.877358%、M4-5-3=5.092688%、M3-10-2=2.983846%、M4-10-2=4.500932%，原每组>=2%均通过。没有计时重跑/放宽门槛。

现有质量session2509从06:21启动，16对32次严格串行、约20分钟，预计06:41结束。每模型5-3物理50/100短30秒seed362/363/364六对、10-2原100长60秒seed362/363两对；分别原4分层均值<=0%、单次<=5%、短>=3/6改善、长>=1/2改善、全部32无缓存完整重解码/独立审计/周期重放有效。M4以保存的自由分仓选择重建固定上下文，再提升回原自由模型，固定/自由都完整审计。质量尚未判定，候选未采用；任何模型条件失败整体恢复评分/归档29新检查，不子集保留。计时期间没有自己的测试/剖析/重解码/其他重负载。检查点保存session2509及原始日志/源码/输入/参数，不重复启动。通过后还需完整回归/四模型公开复核/档案闭合、新60/120秒40分钟确认。小样本CPU证据不称稳定普遍/质量/因果或真实GAP提升。07起收尾08停止本晚安排。

06:43 通用评分简化完成32质量+32无缓存重解码全部审计/重放/距离一致，但M3short50+1.6641pct和M4short+0.3290/long+2.8143/short50+3.1871pct等原质量门槛失败。四CPU组历史通过仍保留，不子集采用、不重跑/放宽、未启动60/120确认。仅恢复评分14579009并归档29新测试；晚期15/lazy/static/共享保持。新完整回归session30471运行，之后公开24和闭合SHA，检查点已更新。

06:50 收尾完成：通用评分整个候选撤回，只评分145恢复/29新测试归档，原晚期15/lazy/static/共享保持。质量32+无缓存32全审计/重放/距离一致，但原M3short50及M4short/long/short50失败；3073passed14warnings74.17s，8公开组24精确复现/全有效，34闭合通过，174原始/48收尾SHA冻结。无活动自有进程。额外19检查核对本版闭合/原始/拥有4及既保留晚期回收试点/确认源码输入登记等全部通过；旧确认清单为嵌套files，辅助平坦读取初次失败原JSON保留，按原结构修正解析、未改旧证据。夜间报告前置全部采用/拒绝总结和GAP/负载/样本限制。下一轮07仅收尾安排、读SHA，勿重跑已闭合时间记录或求解/测试；08前停止或暂停官方有限automation，不跨日。

07:01 本晚正式收尾。只读复核通用48闭合/174原始、恢复拥有4、已保留回收拥有2 SHA保持，无活动自有进程，无重复测试/质量/剖析。官方automation_update将m1-m4-alns更新PAUSED，持久化核对id/kind/name/prompt/原有限RRULE/目标聊天全保留，未创建跨日任务。最终报告VERIFIED、拥有4源码快照和可恢复检查点保存；只保留M3晚期回收有限试点，不称稳定/因果/GAP改善。后续不自动继续。
