# 模型6：静态分批、容器池与两级路线成本联合优化

## 当前基础版：均匀分批、统一分界（2026-10-05）

用 `joint_batch_options.schedule_mode="uniform_cycle"` 启用。周期 `[t0,t1]` 给定，批次数K为正整数决策，重新把**整个周期**均分成K批；每批长度L=(t1-t0)/K。所有批次共用一个相对分界时长d，即 c_b=start_b+d，两个阶段分别长d与L-d。共同分界比例为d/L。订单分批、箱池、路线、车辆及实际服务时刻同时优化。下面的固定候选日程实现作为历史对照保留。

    "joint_batch_options": {
      "schedule_mode": "uniform_cycle",
      "batch_count_candidates": "all_positive_integers",
      "min_phase_minutes": 1,
      "pool_min": 1,
      "pool_max": 131
    }

2026-10-07：省略K范围或设为 `all_positive_integers` 时，允许全部正整数K，由两阶段最短时长推出上界 `floor(H/(2m))`，不受原日历批数限制。各阶段最少1分钟仍为当前场景假设。H的原始刻度不能被K整除时，为该K自动细分整数时间刻度，保持真正等长批次和原始物理时长；结果记录 `time_tick_subdivision`，分钟等于 `ticks*time_tick_seconds/(60*time_tick_subdivision)`。显式K列表用于历史复现或固定K诊断。`allowed_batch_ids`必须覆盖原日历所有批次，避免在重新划分后悄悄丢弃已有批次限制。顾客绝对窗口、货物release_time、物理网络和清洗时长保持输入值。

精确求解枚举K，每个K的MILP只有一个共同分界变量。ALNS枚举K，分界移动同步改变所有批次；两套算法共享各自一次调用的总时间/迭代预算，不按K重复增加预算。ALNS不调用精确模型或Gurobi。只有所有配置K均证明最优或不可行，才报告整体成本最优；限时无解不等于不可行。

求解检查后，ALNS的分界搜索还覆盖实际卸箱、回运加清洗赶上下一批领箱的临界点及其前后刻度，增加有界局部细化与较晚回收初始化；精确模型增加路线时长的有效下界以加强松弛。两者保持相同的业务和成本定义，修改前后采用冻结代码、相同输入/预算/种子的逐例对比，入口为 `experiments.runs.review_model6_search`。短预算改进不能视为所有实例上的性能保证。

[分界搜索检查报告](../../../../output/model6_comparison/20261005_uniform_review_v2/inspection_report.md)保留首轮和预算修正后的全部配对结果，包括改善、退步与限时未找到解的情况。最终分界细化每个K最多预留10%时间，避免反复多候选评价挤占路线搜索。

结果分别记录 `cycle_batch_count`（均匀划分K）、`delivery_batch_count`（有配送的批次）、`actual_recovery_round_count`（实际回运轮数），并保存 `batch_duration_ticks`、`shared_cutoff_offset_ticks`、`shared_cutoff_ratio`、`count_search` 和允许域。独立审计从K重建日历，检查共同分界、两周期车辆与箱子重放、清洗和成本。周期尾部仍须完成清洗，不新增隐含回运轮次。第二阶段可继续回收，截点后的箱子等后续回运。

    .venv/bin/python main.py route --case data/model6/uniform_v1_20261005/lade_0823_10aoi/m6_case.json --solver alns --time 15 --iterations 5000
    python main.py route --case config/cases/20261007_pool25_truck0p5_allK_v1/0_M6.json --all-batch-counts --solver alns --time 300 --iterations 1000000

CLI的 `--candidate-slots` 可切回历史候选日程；均匀模式不兼容逐批 `--optimize-cutoffs` / `--fixed-cutoffs`。已有起点需来自相同的均匀分批允许域，不能直接套用旧候选日程的解。成本系数仍为未校准的合成单位，最低成本不保证箱数最低。

模型五/模型六实验入口会在每次模型五距离求解后，按与模型六相同的费用配置独立计价，自动输出 `cost_report.md`、成本分项、节省比例和多种子统计。模型五的实际箱池费用与原时间表最低需箱量的参考费用分别保存；方案选取遵循各模型的目标。运行方法见[成本对比实验](../../../../docs/model5_model6_cost_comparison.md)。

## 历史候选日程模式

2026-10-04 已实现并注册。候选发运时段固定，联合决定配送批次启用、整单配送批次、实体容器池规模、两级路径、车辆与作业时刻。完整数学范围见 [模型6设计](../../../../docs/model6_joint_batch_cost_design.md)。

- [exact](exact/README.md)：完整联合 MILP，独立建模、提取与成本核对。
- [alns](alns/README.md)：跨批分配与路线搜索、最小可行箱池计算、成本评价。
- 公共输入、投影与独立审计：models/routing/common/cycle/joint.py。
- 共用成本口径与证据校验：models/cost/joint_batch.py。

两个求解器不互相导入；ALNS 无 Gurobi 依赖。固定分仓、同质箱、整单送取、无损失与周期末清洁归仓沿用模型5规则。关闭配送批次仍保留回收轮次，纯取趟次显式计费。成本最优不等于容器数或路径数分别达到各自的最小值。

2026-10-05 增加两窗截点联合优化：候选批次起止给定，截点在显式界内调整。第二阶段仍允许二级车辆配送、取箱和返回前置仓；截点只冻结本轮一级回运资格。截点后入仓的空箱留待后轮，下一批供箱必须等实际总仓卸箱和清洗完成。

## 输入

案例采用 schema_version=2、model=6、multi_batch_cutoff_v1。每笔订单必须显式提供 release_time 和 allowed_batch_ids，保持配送、回收窗为绝对时刻。delivery_batch_id 只用于模型5对照和初始分批。joint_batch_options 支持 pool_min、pool_max、fixed_assignment、optimize_cutoffs 和 cutoff_bounds_minutes。缺少合法批次可由必要条件证明不可行；其余容量与日程可行性由完整求解和审计判断。

optimize_cutoffs 默认为 false，保留历史固定日程。设为 true 后，可为每批提供 cutoff_bounds_minutes，例如 {"b1":[500,700],"b2":[740,950]}（绝对分钟）；下界向上、上界向下转换为整数刻度，必须位于原批次起止之内。省略范围时，两阶段至少保留一个时间刻度；静态生成器默认各保留1分钟，这是显式场景限制，不是实测作业时长。原顾客窗口、批次开始领箱及结束期限不改变。CLI 可用 --optimize-cutoffs / --fixed-cutoffs 切换。

结果的 candidate_schedule 保存原始候选日程，batch_cutoffs 保存实际选择的刻度，batches 和 operating_policy 保存实际日程。独立审计按原输入允许域核验截点，并重新计算全取量、回运、清洗释放、供箱及成本。优化模式结果缺少截点或越界时拒绝审计；历史固定结果仍可读取。

ALNS 支持 --initial-joint-result result.json；先检查原候选起止、物理时间表、供箱、价格与成本，再保留已有可行解。初始解的最优证明不继承。固定/优化截点的对照应使用同一起点、同一新增搜索预算，并单列历史初始化来源与预算。

必须提供 economic_config；所有费率明确给值，null 不会变成0。校准配置需 evidence_file 与逐项匹配证据；当前登记尚未校准人民币价格，不能直接作为可执行费率。合成参数必须声明 synthetic_mechanism 和 synthetic_units。租赁含磨损、清洗或追踪时，禁止重复计费。

当前校准登记支持 CNY，逐项数值和单位必须匹配，来源须有标题、机构、URL、核实日期及核实说明。工时费是整辆车每占用分钟的费用，换算时含声明的人员数；车辆费字段为 e1/e2_vehicle_cost_per_cycle，其评价期为一个给定大周期。代码校验登记的完整性，原始证据是否真实适用仍须研究者核实。租赁池不报告箱体购置投入。

模型6不使用模型5的隐式需求比例池配置。显式 --container-pool-size N 会将模型6池上下限固定为N；自由优化使用 joint_batch_options。单一候选时段也支持。

## 运行与复现

    .venv/bin/python -m tools.data.build_model6_demo_suite
    .venv/bin/python main.py route --case data/model6/mechanism_v1/consolidation_low_pool/m6_case.json --solver exact --exact-time 20
    .venv/bin/python main.py route --case data/model6/mechanism_v1/consolidation_low_pool/m6_case.json --solver alns --time 1 --iterations 1000
    .venv/bin/python -m experiments.runs.run_model6_comparison
    .venv/bin/python -m pytest tests/routing/test_model6_joint.py -q

生成器拒绝覆盖已有非空数据目录；项目已附4个机制例，复现时可直接运行求解/对比命令，或用 --output 指定新的生成目录。所有运行也拒绝覆盖非空输出目录。

[已执行对比](../../../../output/model6_comparison/20261004_mechanism_v1_final/report.md)包含4例、5种精确配置、模型5和模型6各3个ALNS种子，共44次运行。仅合成机制验证，不代表真实经营收益或大规模性能。

用户指出该组规模过小后，新增原项目LaDe派生的10/20/30区域静态场景，需求为131/201/255箱次，进行了36次对比运行。见 [较大场景的箱数与批次](../../../../output/model6_comparison/20261004_lade_static_v2/resources.md)。模型5预设池与其所得时间表的最小可行池分开统计，原分批下的成本重优化单独保留。已达到的箱数下界和固定箱池下的批次下界可以单独证明，不能代替成本最优证明。

当前V2全部186个多批次配置由 `tools/data/build_model6_static_suite.py --all-multibatch` 适配，覆盖93个基础场景各自的B2/B3、6–100个配送点和6–255箱次需求。模型5距离目标、模型6原分批成本对照、联合模型6各尝试一次精确法和三个ALNS种子，共2232次计划运行；首轮统一采用精确3秒、ALNS每种子2秒、4个并发进程。全量短预算结果见 [汇总](../../../../output/model6_comparison/20261004_static_v2_full/full_summary.md)，逐例箱数/批次、全部状态及来源证据均保留。分析入口为 `experiments.analysis.summarize_model6_full_suite`，重新审计并区分箱数、批次、路线取舍和成本证明；短预算不能称收敛性能试验，合成系数不能称实测经济收益。
