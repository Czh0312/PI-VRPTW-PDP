# 2E-VRPTW-PDD

当前研究：静态全信息、固定订单批次的两级配送与容器循环。
正式执行规则为 `multi_batch_cutoff_v1`，B=1 是单批次，B>1 是多批次。

## 模型状态

| 编号 | 研究角色 | 当前状态 |
| --- | --- | --- |
| M1 | 一次性包装、固定分仓、纯配送 | 新版 B=1 已实现；MILP 与 ALNS |
| M2 | 一次性包装、自由分仓、纯配送 | 新版 B=1 已实现；分仓枚举＋MILP / 分仓搜索＋ALNS |
| M3 | 单批次循环包装、固定分仓 | B=1，含清洗、有限池与周期闭合 |
| M4 | 单批次循环包装、自由选仓 | B=1，含选仓、截止全取、清洗与周期闭合 |
| M5 | 多批次循环包装、固定分仓 | B>1，含跨批复用、清洗、有限池与大周期闭合 |

四模型已接入统一单批规则并完成小例验证；大规模正式算法评测与真实经营验证尚未完成。
固定分仓的包装对照应为 M1/M3，自由分仓应为 M2/M4；分别研究 M1/M2、M3/M4 的分仓价值。
新版 M5 是当前多批次模型。旧 M5 中转模型、旧 M6–M8、旧实验和旧经济结论已退出主线。清理细节见 [清理记录](docs/repository_cleanup_20260929.md)。

## 运行

```sh
# 默认运行新版单批机制例
.venv/bin/python main.py route --time 2

# 四模型单批对照与条件经济分析（费用为演示参数）
.venv/bin/python main.py compare-single-batch --time 1 --exact-time 10

# 多批次机制例
.venv/bin/python main.py route --case config/cases/multibatch_demo.json --time 2

# 列出新版单批数据，不启动求解
.venv/bin/python main.py compare --dataset-manifest data/manifest.json --batch-counts 1 --list-only

# 对已保存并通过审计的方案计算费用；当前参数仅用于算术演示
.venv/bin/python main.py cost --route-result /path/to/result.json --economic-config config/cost/cases/multibatch_synthetic.json

# 保留算法的历史基线只能显式调用，结果不用于新版四模型对照
.venv/bin/python main.py route --baseline --case config/baselines/zhou_small_demo.json --model 4 --time 2

.venv/bin/python -m pytest -q
```

精确求解依赖 Gurobi 及许可证；新版 ALNS 不依赖 Gurobi。单批次不等于一天，周期必须按输入日程解释。

单批次 M1/M2 的根节点列生成下界、标签/MILP 精确定价及等总预算混合求解试验，见
[列生成原型与证明边界](docs/single_batch_columns_20261001.md)。该原型只在显式实验入口启用。
利用列生成路线池构造可行初解的后续优化，见 [路线池排程与同预算对照](docs/single_batch_route_pool_20261001.md)。
后续[时间必要条件与分仓对偶约束试验](docs/single_batch_dual_cuts_20261001.md)记录下界强化、退步方案和同预算验证。
[批量定价效率试验](docs/single_batch_pricing_pool_20261001.md)继续检查根节点提速能否改善端到端精确求解。
[一级货流汇总试验](docs/single_batch_compact_pool_20261001.md)进一步缩小 M1/M2 初解路线池，并保留同池速度退步的对照结果。
[热启动排程整理试验](docs/single_batch_start_20261001.md)验证同质车辆编号和最早排程，尚未测得稳定提速，保留显式实验选项。
[路线池时间约束试验](docs/single_batch_pool_bounds_20261001.md)收紧供货与多趟时间界，并合并每个车辆位置的时间约束，保留各版本的完整对照结果。
[定价时间分解试验](docs/single_batch_pricing_schedule_20261001.md)修复空窗口任务的子问题处理，比较连续时钟与按需时间禁用约束，并验证每条禁用约束的有效性。
[路线池车辆排序试验](docs/single_batch_pool_symmetry_20261001.md)进一步检查同质车辆搜索对称性，保留分数解反例、两版实现及同预算对照。
[完整整数模型二环与次序边界试验](docs/single_batch_causal_bounds_20261001.md)覆盖 M1–M4，记录两个有效但未测得稳定提速的候选和 30 客户 M4 补测。
[夜间标签定价下界试验](docs/single_batch_label_bound_20261002.md)记录返仓下界、批量发现两版实现、定价搜索减少及端到端退步，候选默认关闭。
[固定初解与对偶约束消融](docs/single_batch_certificate_ablation_20261002.md)以三种子分离约束集合影响，并独立核查全部保留平面；属于离线诊断，不作默认采用。

单批次 M1–M4 的修复顺序、周期重启及上下文复用试验，见
[修复与重启优化记录](docs/alns_single_batch_repair_20261001.md)，含完整退步、撤回门槛与最终回归。
下降邻域的车辆就绪与显式等待筛选修正，见
[筛选修正与大规模验证](docs/alns_single_batch_readiness_20261001.md)，含确定性漏搜反例、质量波动与耗时代价。
后续[regret修复试验](docs/alns_single_batch_regret_20261001.md)已撤回；
[M3下降邻域距离缓存](docs/alns_single_batch_descent_cache_20261001.md)通过另两来源的固定工作量复核与等时质量检查，已保留。
后续[M3修复阶段距离缓存](docs/alns_single_batch_repair_geometry_20261001.md)记录两个撤回候选及M3单独确认，保留通过验证的M3提速。
已采用[单批次静态载荷摘要复用](docs/alns_single_batch_load_cache_20261001.md)降低M1–M4完整解码开销，并记录逐模型质量波动及验证结果。
后续[M3早期成对交换](docs/alns_single_batch_exchange_20261001.md)与[零罚项快速分支](docs/alns_single_batch_penalty_fastpath_20261001.md)均因等时质量退步撤回，保留前述求解实现及完整试验记录。
最新[M3回收插入前缀复用](docs/alns_single_batch_lazy_pickup_20261001.md)通过CPU与等时质量验证，含三项撤回原型、全部退步和四模型复现结果。
本轮[四项续试与撤回记录](docs/alns_single_batch_continued_attempts_20261001.md)保留未达双来源CPU门槛的方案和恢复版本验证；没有新增采用的优化。
后续[静态准备与M2成组换仓试验](docs/alns_single_batch_route_groups_20261001.md)记录短预算改善但长预算未通过的两版搜索策略及全部撤回结果。
本次[M2容量约束尾段重组](docs/alns_single_batch_route_recombine_20261001.md)在长预算试验改善、短预算未通过，候选已撤回；输入加载故障和补充实验单独记录。
后续[M2审计热启动与末尾下降](docs/alns_single_batch_audited_route_descent_20261001.md)均因长预算未通过撤回，含有效候选稀疏的诊断、全部32次质量运行和恢复版验证。

静态多批次的池规模扫描、清洗敏感性、冻结运行和证明状态汇总，见
[代码与实验入口](docs/static_multibatch_implementation.md)。
M5 的距离下降邻域、稳定初始搜索及 50/100 顾客同预算对照，见
[M5 ALNS 优化与完整结果](docs/model5_alns_optimization_20260930.md)。
后续一级解码复用、分仓邻域缓存及新母例验证，见
[M5 第二轮优化](docs/model5_alns_round2_20261001.md)。
搜索停滞、预算延长与探索分支下降的未采用试验，见
[M5 第三轮优化](docs/model5_alns_round3_20261001.md)。
距离枚举的可选有向弧缓存、完整对照及默认关闭决策，见
[M5 第四轮记录](docs/model5_alns_round4_20261001.md)。
车辆前序时刻误筛修正、默认采用门槛及全部改善/退步，见
[M5 第五轮优化](docs/model5_alns_round5_20261001.md)。
后悔值修复的三轮开发试验未达到总体质量门槛，默认入口已恢复，见
[M5 第六轮试验与后续方向](docs/model5_alns_round6_20261001.md)。
首轮逐例 5% GAP 目标与联合路线池诊断（当时两组 50 顾客达标，100 顾客未达标），见
[M5 GAP 目标记录](docs/model5_gap_target_20261001.md)。
五仓完整一级枚举、可选双向对换及 100 顾客认证 GAP 继续下降，见
[M5 GAP 第二轮](docs/model5_gap_round2_20261001.md)。
两组 100 顾客的认证 GAP 已进入 5% 以内，批量定价、两趟重组及条件供货联合下界见
[M5 GAP 第三轮](docs/model5_gap_round3_20261001.md)。
统一混合入口、从头求解及跨实例验证（原例最好 3.35%/4.11%，新增例尚未达标），见
[M5 GAP 第四轮](docs/model5_gap_round4_20261001.md)。
跨仓预算分配与批次定价余量复用，使新增例紧池 GAP 降至 2.11%、全池降至 8.58%，见
[M5 GAP 第五轮](docs/model5_gap_round5_20261001.md)。
可选物理趟次和联合多批供货条件，使上述新增例全池/紧池认证 GAP 进一步降至 **4.72% / 1.85%**，两组均进入 5% 内，见
[M5 GAP 第六轮](docs/model5_gap_round6_20261001.md)，含全部暖启动追加预算与 181 项回归；尚未验证从头同预算稳定达标。
自动多批供货组合和候选上限已接入统一入口，全池最好认证 GAP 降至 **3.04%**；660 秒冷启动对照为 23.57% / 21.65%，见
[M5 GAP 第七轮](docs/model5_gap_round7_20261001.md)，含独立证书投影、188 项回归和完整未达标结果。

## 数据与经济结论

- 现行数据为 `static_batch_suite_v2`：日内周期、分规模固定车队、独立生成的使用与回收时间、同源资源配对和有限箱池扫描均已修订，见 [修订与验收](docs/dataset_redesign_v2_20260930.md)。
- `data/single_batch/`、`data/multi_batch/`：各自按 `small/medium/large` 管理，Zhou 主对比集按小型 7/10/12、中型 20/30、大型 50/100 顾客划分，每种顾客数 10 例（5 个来源块×2 个情景），共 70 个单批与配对的 140 个多批配置；单批用于 M1–M4 对照，多批用于 M5。
- `data/shared/` 保存同一场景的共同网络、车队和来源；`data/manifest.json` 加上 LaDe 与人工机制补充数据，共索引 93 组 B1/B2/B3 配对。见 [数据目录与规模说明](data/README.md)。
- `data/real_case/`：保留 LaDe 配送派生资料、OSRM 道路矩阵和来源说明，供追溯与重建。
- `data/zhou_later_pickup/`：保留基准来源与派生记录，不等同于当前统一规则的输入。
- 当前仍缺实际容器回收、清洗、损耗、寿命和费用观测。真实配送基础不能被写成完整真实闭环数据。
- 新版一次性/循环包装同口径对照、寿命补购、回本条件及价格/寿命敏感性已接入；当前费用仍为虚构演示，实际经济性未验证。旧回本期不适用于当前模型。
- 见 [单批四模型与条件经济性](docs/single_batch_comparison.md)，含验证结果与可重现命令。

## 目录

- `models/routing/variants/model_1/`～`model_5/`：五个编号模型，每个内部都有独立 `exact/`、`alns/` 文件夹。
- `models/routing/solvers/exact/`：精确求解的 MILP、解提取和自由选仓穷举。
- `models/routing/solvers/alns/`：ALNS 搜索、初解、候选评价和自由选仓启发式。
- `models/routing/common/cycle/`：共用输入、日程规则、结果格式、分仓转换与独立审计，不包含求解算法。
- [模型位置与编号](docs/model_layout.md)：完整目录树、单批/多批范围及旧结果迁移说明。
- `models/routing/baselines/`：隔离的历史 M1–M4 数学模型和算法；新版自由分仓与包装对照的迁移素材。
- `models/routing/common/`、`models/routing/alns/`：共享数据和保留基线使用的通用工具。
- `config/cases/`：当前单批/多批示例；`config/baselines/`：显式历史案例。
- `experiments/runs/run_multibatch_cycle.py`：固定批次池规模实验；`run_single_batch_comparison.py`：四模型单批与经济对照。
- `output/`：当前统一策略与数据验收结果；历史结果在清理归档中。
- `archive/cleanup_20260929/`：清理前快照、文件哈希和操作清单。

参见 [当前计划](docs/static_single_multibatch_study_plan.md)、[模型使用说明](docs/model5_multibatch_cycle.md)和 [数据状态](docs/data_status.md)。
