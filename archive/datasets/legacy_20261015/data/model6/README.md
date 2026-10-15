# 模型6数据：程序机制例与较大静态场景

## M5/M6共享主对比（2026-10-05）

两模型的新配对实验统一使用 `data/model5_model6/shared_v1_20261005/manifest.json`，395个场景逐例共用订单、网络、资源、周期和费用文件。M5固定参照日历/分批/N，M6优化这些决策；另含相同M5策略下的成本目标对照。使用 `experiments.runs.run_model5_model6_shared`，见 [设计与验收](../../docs/model5_model6_shared_dataset_20261005.md)。以下M6独立数据与历史实验保留各自用途。

## 新均匀分批数据集（2026-10-05，uniform_v2）

`uniform_v2_20261005/manifest.json`新增395个配置：91个Zhou/LaDe来源场景各三档独立预约窗口、10个规模代表场景各12个敏感性变体，以及2个保留机制对照。覆盖7–100顾客、同点多订单、分时到货、清洗、车辆、成本和K范围变化。同源B2/B3去重；原网络、逐顾客箱量及货重守恒，旧数据不覆盖。

本版预约规则独立于K与参考路线，使用 `experiments.runs.run_model6_dataset` 运行同成本、同物理输入的逐项决策对照。不可行场景和限时无解均保留并区分。默认35/90/180分钟窗口、合成费用及其它假设、生成/审计/运行命令见 [设计与验收说明](../../docs/model6_uniform_dataset_v2_20261005.md)。旧均匀版及固定日历结果继续作为历史记录。

## 均匀周期基础版（2026-10-05）

`uniform_v1_20261005`保留原订单、顾客绝对送取窗、release_time、物理网络、车队、清洗及大周期起止。M5保留原固定日历；M6在整个周期上优化K、均匀划分批次，各批共用一个相对分界。默认搜索1至原候选批次数，可显式扩展；每阶段最短1分钟为场景假设。适配记录声明重新划分日历，原输入与历史结果不覆盖。

    .venv/bin/python -m tools.data.build_model6_static_suite --uniform-cycle --batch-count-candidates 1 2 3 --families mechanism_cutoff_choice zhou_Set1_7-2-1 lade_0823_10aoi lade_0823_30aoi --output /path/to/new/uniform_suite
    .venv/bin/python -m experiments.runs.run_model6_uniform_comparison --manifest data/model6/uniform_v1_20261005/manifest.json --exact-time 24 --time 10 --seeds 42 43 --workers 2 --output /path/to/new/uniform_study

成本仍为synthetic_units。全周期划分数K、配送批次数和实际回运轮数分别记录。均匀基础版与原固定候选日历的逐批分界扩展具有不同日历允许域，不能直接声称其中一个的最优成本一定更低。

## 两窗截点优化（2026-10-05）

cutoff_v1_20261005 包含7个B3代表场景：两个人工机制、Zhou的7/20/100点、LaDe的10/30区域，需求范围6–255箱次。原订单、车队、路网、顾客时间窗及批次起止保持不变；模型6显式允许在原批次内调整截点，两窗各至少1分钟。第二阶段仍可末端回收，截点只冻结本轮一级回运资格。模型5保留原固定截点。

    .venv/bin/python -m tools.data.build_model6_static_suite --families mechanism_forced_late mechanism_cutoff_choice zhou_Set1_7-2-1 zhou_Set2_100-5-1b_subset20 zhou_Set2_100-5-1b lade_0823_10aoi lade_0823_30aoi --batch-count 3 --optimize-cutoffs --min-phase-minutes 1 --output /path/to/new/cutoff_suite
    .venv/bin/python -m experiments.runs.run_model6_cutoff_comparison --manifest data/model6/cutoff_v1_20261005/manifest.json --initial-study output/model6_comparison/20261004_static_v2_full --exact-time 30 --time 10 --iterations 30000 --seeds 42 43 44 --workers 4 --timeout-overhead 20 --output /path/to/new/cutoff_comparison

对照为M5距离、固定截点M6、优化截点M6，7例共84次尝试。两组M6 ALNS使用相同已审计历史起点、同一新增预算；初始化来源及历史预算单列。全部价格保持原合成费率，不做人民币节约结论。与旧固定时窗结果分别保存，不覆盖历史数据。

## 全部V2多批次配置

static_v2_full_20261004 覆盖 data/manifest.json 中全部186个多批次配置，即93个基础场景各自的B2/B3：Zhou派生140个、LaDe派生42个、人工机制4个，6–100个配送点/区域、6–255箱次需求。逐例保留原物理输入，不混入单批次B1或历史V1结果。同一基础场景的不同批次数不是独立样本。

    .venv/bin/python -m tools.data.build_model6_static_suite --all-multibatch --output /path/to/new/full_suite
    .venv/bin/python -m experiments.runs.run_model6_comparison --manifest data/model6/static_v2_full_20261004/manifest.json --treatments M5_distance M6_pool_only M6_joint --alns-ablations --exact-time 3 --time 2 --iterations 30000 --seeds 42 43 44 --workers 4 --timeout-overhead 20 --output /path/to/new/comparison
    .venv/bin/python -m experiments.analysis.summarize_model6_full_suite --directory /path/to/comparison

[全量汇总](../../output/model6_comparison/20261004_static_v2_full/full_summary.md)按来源、配送点数量和候选批次数统计箱数减少、相同、增加，以及配送批次变化。共2232次计划运行，精确与ALNS均保留限时和失败记录。每个案例以模型5已得时间表的最小需箱量作保守箱数对照，而非预配N=Q；模型6按已得联合方案最低测试成本选取，不能将其称为箱数最少的方案。

这是一轮统一短预算筛查；并发竞争与初始化可能影响结果，不视为算法收敛证据。费用仍是synthetic_units，Zhou坐标距离不是真实公里，不作人民币节约结论。

## 较大静态对比

用户指出1–4单机制例不能支撑主要对比后，新增 static_v2_lade_20261004。使用原项目同一天的10、20、30个AOI场景，分别为131、201、255箱次需求。保留原需求箱量、重量、客户和固定分仓、绝对送取窗、服务时长、车队、道路矩阵与候选日程；新增显式 release_time=周期开始、allowed_batch_ids=原候选批次全集。货物全部可领用是研究假设，未称为实测。

包裹数量和区域坐标来自既有LaDe派生底稿；一包裹一箱、预约窗口、回收、车型和清洗继承原场景假设。每个转换目录保存来源路径、全部原文件哈希和改变的字段，未将原小例的箱量乘倍数。费用采用所有实例一致的合成配置，未校准为企业账单。

    .venv/bin/python -m tools.data.build_model6_static_suite --output /path/to/new/suite
    .venv/bin/python -m experiments.runs.run_model6_comparison --manifest data/model6/static_v2_lade_20261004/manifest.json --treatments M5_distance M6_pool_only M6_joint --alns-ablations --exact-time 15 --time 8 --iterations 30000 --seeds 42 43 44
    .venv/bin/python -m experiments.analysis.summarize_model6_resources --directory /path/to/comparison

[较大场景箱数与批次结果](../../output/model6_comparison/20261004_lade_static_v2/resources.md)单独列示模型5预配池、所得路线的最小池、各次方案最小需要以及模型6池和配送批次，避免把预配池直接当作模型5最低需要。精确限时无解保留；资源下界证据与成本最优证明分开。

## 程序机制例

mechanism_v1 包含4个静态对照算例，共用固定候选时段和绝对服务窗。订单、坐标、车队和费用均为人工机制数据，价格单位 synthetic_units，不是企业采样或真实人民币参数，也不是将模型5人民币费率按比例放大或缩小。

| 算例 | 订单数 | 机制 |
|---|---:|---|
| consolidation_low_pool | 2 | 箱池占用费用较低，比较合批减少路线与保持分批的取舍 |
| reuse_high_pool | 2 | 其他输入不变，仅将每箱周期池费由5调整为30，检查跨批复用 |
| late_return_inactive_delivery | 1 | 配送必须在早批完成，空箱晚于早批截止；未启用配送的后批仍须纯回收 |
| two_satellites | 4 | 多仓路线、物理车辆与跨批箱池联合选择 |

其他费率为：两级距离各1/公里，占用工时各0.1/分钟，一级/二级物理车辆每周期10/5，每趟各1，清洗0.2/箱次，追踪0.1/箱次。箱购价10、使用次数50仅用于人工磨损分项；购置投入单列，磨损按全部需求箱次计算。每坐标单位按1公里、行驶速度1公里/分钟定义人工网络。

所有订单释放时刻和允许批次均显式记录。模型5对照固定原分批且箱池等于全部需求；模型6自由选择池，但窗口、车队、候选时段和回收机会保持一致。对比脚本另含固定池/分批的同成本对照，防止把目标变化或原池配置影响全部归为分批收益。

生成器：tools/data/build_model6_demo_suite.py；对比脚本：experiments/runs/run_model6_comparison.py。生成器和运行器拒绝覆盖非空目标目录。完整结果见 [对比报告](../../output/model6_comparison/20261004_mechanism_v1_final/report.md)。
