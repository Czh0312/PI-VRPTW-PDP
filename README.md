# 2E-VRPTW-PDD

本项目研究静态全信息下，两级配送系统中可循环容器的长期经济可行性，以及自由选仓和多批次周转可能带来的进一步优化。
正式执行规则为 `multi_batch_cutoff_v1`，B=1 是单批次，B>1 是多批次；周期长度由输入日程决定。

[文档导航](docs/README.md) · [开发指南](docs/development.md) · [模型布局](docs/model_layout.md) · [数据说明](data/README.md) · [实验入口](experiments/README.md) · [结果索引](output/README.md)

当前数值实验统一使用[小7、中20、大50顾客的数据集](data/formal_benchmark27_uniform_7_20_50_v1/README.md)，
每档9例，共27例、216个模型入口。模型3与模型8的ALNS实验先选每档3例，
以[当前实验方案](config/experiments/m3_m8_uniform_7_20_50_v1/README.md)为准；目前保持停止。
其他历史算例与旧配置已移入[归档](archive/datasets/retired_20261024/README.md)。

## 研究主线

1. **M1、M3 基础模型与经济成本模型：验证可循环容器的长期经济可行性。** 在固定分仓、单批次及相同需求和服务条件下，M1 求一次性纸箱的配送路径，M3 求循环容器的配送与回收路径。将两者的路径、工时和车辆等结果输入经济成本模型，加入包装购买、清洗、追踪及寿命补购费用，计算未来重复运营的累计成本，识别何时回本、何时开始节省，以及哪些价格和寿命条件能够支持长期使用循环容器。
2. **M2、M4 自由选仓扩展：探索分仓决策的进一步优化。** M2 在 M1 基础上允许自由选仓，M4 在 M3 基础上允许自由选仓。通过 M1/M2、M3/M4 分别识别纸箱和循环容器方案中的选仓价值；再将 M2/M4 的结果输入同一经济成本模型，评价自由选仓是否改善循环容器的回本周期和累计节省。
3. **M8 一般模型及 M5、M6、M7 受限特例：探索多批次的优化潜力。** 在固定分仓下，以 M3 为单批次循环基线，比较预设多批制度 M5、等长共同分界 M6、等长独立分界 M7 与不等长独立分界 M8。重点分析周期内回收、清洗后再次投用能否减少实体箱池，以及这种节省与运输、车辆和作业代价之间的取舍；通过受限模型识别订单分批、批次数、批内分界和批次长度各自带来的改善。多批次是否更经济由结果判断。

最终联合模型为 **M9：在 M8 上加入 M4 的顾客级自由选仓**，统一决策分仓、分批、
不等长日历、逐批截点、箱池和两级路线。当前先完成精确求解，数学公式与 M8 特例关系见
[模型9](docs/model9_free_multibatch.md)，已完成[单种子小算例](output/experiments/model9_small_exact_20261030/README.md)。
另已测试[现有7顾客S02](output/experiments/model89_S02_exact_20261030_v1/README.md)：
固定K=2诊断找到较M8最优值低8.3683%的M9可行方案；M9全域最优性尚未证明。
已补测[现有S03/S04](output/experiments/model89_other_small_exact_20261030_v1/README.md)：
双方固定K=2的限时可行方案成本分别降低29.7758%、29.9282%；M9全域最优性仍未证明。

## 模型及其用途

| 编号 | 模型设定 | 研究用途 | 求解方式 |
| --- | --- | --- | --- |
| M1 | 一次性包装、固定分仓、纯配送，B=1 | 提供纸箱路径基线，供经济成本模型核算 | MILP / ALNS |
| M2 | 一次性包装、自由分仓、纯配送，B=1 | 与 M1 比较纸箱方案的自由选仓价值 | 联合分仓 MILP / ALNS |
| M3 | 循环包装、固定分仓、配送与回收，B=1 | 与 M1 评价长期包装经济性，并作为多批次研究的单批基线 | MILP / ALNS |
| M4 | 循环包装、自由分仓、配送与回收，B=1 | 与 M3 比较自由选仓价值，与 M2 评价包装经济性 | 联合分仓 MILP / ALNS |
| M5 | 固定分仓、订单分批及日历，B>1；联合优化箱数与调度成本 | 评价预设多批制度，并作为 M8 固定分批和日历的特例 | MILP / ALNS |
| M6 | 固定分仓；优化订单分批、批次数、箱池和路线，等长批次、共同分界 | 评价均匀分批与共同分界制度 | MILP / ALNS |
| M7 | 固定分仓；优化订单分批、批次数、箱池和路线，等长批次、逐批独立分界 | 与 M6 比较逐批独立分界的价值 | MILP / ALNS |
| M8 | 固定分仓；联合优化订单分批、批次数、批次长度、分界、箱池和路线 | 作为一般模型，与 M3/M5/M6/M7 比较多批次及日历优化的潜力 | MILP / ALNS |
| M9 | 自由选仓；联合优化分仓、订单分批、批次数、批次长度、分界、箱池和路线 | 统一自由选仓与多批次，固定顾客分仓可得到相应 M8 | 联合 MILP（当前先做精确） |

在相同物理数据、费用、时间刻度和允许域下，M8 固定订单分批、批次数、起止及批内分界可得到 M5；限制各批等长可得到 M7，进一步令各批共用相对分界可得到 M6。M3 对应固定单批次的基线；检验其与 M8 的特例关系时，还须固定相同的批内分界和箱池。M5 的给定日历应属于 M8 的允许域，但不必满足 M6 的等长规则。详见 [M5 决策边界](docs/model5_pool_optimization.md)和[日历设计](docs/model7_model8_calendar_design.md)。

M3 另提供显式成本目标 `--objective-mode cost`，直接使用与 M5–M8 相同的费用配置优化，
用于第三条研究主线中的单批次与多批次成本对照；M1/M3 基础包装经济性研究保留先求路径、再做长期成本核算的流程。默认距离模式保留，用法与边界见[模型3成本对照](docs/model3_cost_comparison.md)。
M4 精确求解另支持显式 `--m4-strengthening capacity_activation`，加入客户集合容量、仓库连通性及逐趟启用容量联动；默认保留原表达 `none`。更紧的下界不保证冷启动更快找到好路径，使用方式、有效性证明及对照结果见[M4表达强化](docs/model4_strengthening.md)。
循环模型包含回收、清洗、有限箱池与周期闭合。M6–M8 的允许批次数由周期和最短阶段约束确定；显式 K 子集只覆盖相应诊断范围，见[日历设计](docs/model7_model8_calendar_design.md)和[批次数域说明](docs/cost_and_batch_domain_20261007.md)。

已保存[单批 M1–M4 对比](output/experiments/m1_m4_comparison_20261004/m1_m4_solver_comparison_20261004.md)、[M5 全规模补测](output/experiments/m5_comparison_20261004/m5_solver_comparison_20261004.md)及[M3/M5/M6/M7/M8 成本对比](output/model35678_comparison/README.md)。各报告分别记录预算、种子、失败和证明范围；当前尚缺完整真实闭环经营数据。
历史 M1–M4 仅通过 `--baseline` 调用；已归档的旧 M5–M8 与当前编号定义不同。

## 经济成本模型与未来回本

经济成本模型接收 M1/M3 的路径及作业结果，评价相同需求在未来重复运营时的累计支出与节省；M2/M4 可按相同口径进一步评价。它承担第一、二条研究主线的长期经济性核算，与第三条主线中直接选择路线和日历的成本优化目标分开说明。路线约束和独立审计检验物理可行性，成本模型检验给定价格、寿命和评价期下的经济可行性。

每期分别核算两级运输距离费用、车辆占用工时费用和实际启用车辆费用，三者费率覆盖的成本不得重复。纸箱每期另计购买和处置；循环容器另计实际回收箱次的清洗整理、实际发箱次数的追踪，并单列实体箱池的初始购买及寿命到期补购。

以当前单批次购置口径为例，设 `S` 为纸箱方案每期总费用，`R` 为循环方案每期运营费用（含回收、清洗和追踪，不含购箱），`I=N×单箱购置价` 为初始投入，`L` 为整池更新间隔，`T` 为评价周期数：

```text
纸箱累计费用 = T×S
循环累计费用 = T×R + I×ceil(T/L)
循环累计节省 = T×(S−R) − I×ceil(T/L)
```

逐期累计节省首次达到非负时为回本点，严格为正时开始产生净节省；同时报告回本后至评价期末持续不再亏损的起点、单位配送成本，以及纸箱价格和容器寿命的敏感性。在首次补购前，若 `S>R`，回本所需周期为 `ceil(I/(S−R))`；该周期若超过寿命区间，须按补购后的逐期累计重新判断。评价期内未回本时如实记录，不预设循环容器一定更经济。

上述现金核算假设每期需求、服务和方案重复，无随机损耗、折现、融资、通胀及残值。初始购买与补购已进入现金累计，不能再叠加同一笔箱体摊销；多批次按使用次数分摊的箱体费用与实际补购现金流另行区分。当前结论是给定方案和参数下的条件结果，完整公式、实现及边界见[单批经济性说明](docs/single_batch_comparison.md)、[周期费用核算](models/cost/multibatch.py)与[累计回本计算](models/cost/single_batch.py)。

## 快速运行

在仓库根目录执行。新环境的安装步骤见[开发指南](docs/development.md)。

```sh
# 当前S01单批示例（7顾客，M3，ALNS）
.venv/bin/python main.py route --time 2

# 当前S01多批示例（7顾客，M5，ALNS）
.venv/bin/python main.py route --case config/experiments/examples/multibatch_demo.json --time 2

# M8 日历优化示例；使用案例中声明的费用与允许域
.venv/bin/python main.py route --case config/experiments/examples/model8_demo.json --time 2

# M9 自由选仓多批次精确小例；人工参数、完整 K 域和一个种子
.venv/bin/python main.py route --case config/experiments/examples/model9_demo.json --solver exact --seed 42

# 单批四模型：M1/M3 基础经济性、M2/M4 自由选仓扩展及累计回本（示例费用）
.venv/bin/python main.py compare-single-batch --time 1 --exact-time 10

# 正式27例配对数据：检查输入，不启动求解
.venv/bin/python -m tools.audit.audit_formal_benchmark27

# 已审计方案的费用核算
.venv/bin/python main.py cost --route-result /path/to/result.json --economic-config config/cost/synthetic/multibatch_synthetic.json

# 日常基础检查（完整回归用 make test-full）
make test
```

精确求解需要 Gurobi 和有效许可证；ALNS 求解入口不加载 Gurobi。
当前精确入口默认直接 Gurobi，M6–M8 的批次数、日历、路线与箱池在同一个 MILP 中优化，
旧精确算法已从当前代码移除并存入[研究归档](archive/exact_research_20261019/README.md)。
不使用外层 K 搜索或定制强化。数学表达见[统一批次数 MILP](docs/unified_batch_count_milp.md)。
单次输出默认写入 `output/single_runs/`，可用 `--output` 指向新的空目录。

## 目录职责

| 目录 / 文件 | 内容 |
| --- | --- |
| `main.py`、`cli/` | 命令分发、单次执行与结果输出 |
| `pyproject.toml`、`Makefile` | 依赖、测试配置与日常检查命令 |
| `models/routing/model_1/`～`model_9/` | M1–M8 各有独立 `alns/`、`exact/`；M9 当前有独立 `exact/`，见[逐文件说明](models/routing/README.md) |
| `models/common/` | 两种算法共用的数据、业务规则、结果与独立审计 |
| `archive/routing_baselines/` | 显式调用的历史 M1–M4 及旧搜索组件 |
| `models/cost/` | 路径结果的费用核算、生命周期累计回本，以及多批次优化使用的费用定义 |
| `config/` | 按车辆、容器、运行规则、成本、求解器、数据生成、实验组合分类的参数 |
| `tools/configuration/` | 配置读取和路径解析的 Python 代码 |
| `data/` | 正式27例数据集、八模型配对配置和入口说明 |
| `experiments/` | 批量运行与结果分析的程序；生成文件写入 `output/` |
| `tools/` | 数据构造、审计、恢复与公用绘图（`tools/reporting/`） |
| `tests/` | 路由、费用和数据回归测试 |
| `docs/` | 当前计划及必要的模型、数据和使用说明 |
| `project/` | 聊天总结 skill 生成的交接记录 |
| `output/` | 唯一成果目录：实验结果、图片、报告、Excel 及复现材料 |
| `reference/`、`archive/` | 参考材料与历史归档 |

## 数据与研究记录

其他电脑的拉取、安装与运行步骤见 [正式数据远程运行说明](docs/benchmark27_remote_run.md)。

现行物理算例使用 [formal_benchmark27_spacetime_v4_1](data/formal_benchmark27_spacetime_v4_1/README.md)：小、中、大各9个，共27个基础算例。小规模9例统一为7位顾客；中规模仍为20/30位，大规模仍为50/100位。两、三、四个外部需求时段各9例，卫星站数量覆盖1—5个。每位顾客仍一个订单、一个标准箱。小规模分别从v3同编号10顾客算例中按顾客编号数值排序取前7位，保留所选顾客的坐标、完整货重、时间窗与分仓，以及全部仓点和车辆；中大规模物理输入保持。8个模型共用27个基础算例，对应216份运行配置。9个小规模来自6个原始来源，3对同源7顾客子集各重叠5位顾客，不能当作独立来源。旧 [spacetime_v3](data/formal_benchmark27_spacetime_v3/README.md)、[spacetime_v2](data/formal_benchmark27_spacetime_v2/README.md)、[one_box_v1](data/formal_benchmark27_one_box_v1/README.md) 及其结果保留；[formal_benchmark27_v1](data/formal_benchmark27_v1/README.md) 保留为多箱需求情景。

后续ALNS默认每个模型、每个算例只运行一个种子42；额外种子需明确指定。

本版算例已于2026-10-23经用户确认固定，后续实验统一使用 `formal_benchmark27_spacetime_v4_1`。版本哈希和规模配额见[冻结登记](config/experiments/benchmark27_spacetime_v4_1/frozen.json)；调整顾客集合或规模须另建版本，不覆盖本版输入。

7顾客复测已完成：M1—M4共36次300秒冷启动，已证最优分别为9/9、9/9、8/9、7/9。M4另用30秒ALNS初解与`capacity_activation`强化，共享每例300秒总预算，9/9证明最优，最长92.57秒；S01/M3另有基于M4下界的补充最优性证明，原冷启动状态保持。完整结果、证明范围与核验见[小规模重测报告](output/experiments/small7_solver_comparison_20261023_v1.md)。

后续对原3次未证最优调用另做600秒原式冷启动，3/3均直接证明最优。独立60秒ALNS、种子42：M1—M3各9/9达到已知最优值，M4为6/9，M4平均GAP为0.717%、最大3.436%；ALNS匹配参考值不等于算法自身给出最优证明。实际耗时、逐例差距及核验见[600秒精确与60秒ALNS报告](output/experiments/small7_exact600_alns60_20261023_v1/comparison.md)。全部36个小规模距离参考值现均有[独立MILP最优证明](output/experiments/small7_exact600_alns60_20261023_v1/optimal_references.json)，原记录保留。
ALNS内部记录的首次达到已知最优值平均时间：M1为0.14秒、M2为7.87秒、M3为5.83秒、M4为5.88秒。M1—M3各统计9例，M4仅统计成功达到的6例，另3例在60秒内未达到，不计入该均值；各次调用仍按60秒预算运行。

容量保持v3配置：一级载重为体积容量×20；小规模统一一级2辆、容量10／载重200，二级共4辆、每辆3箱／60；中大规模二级每辆6箱／120。只删除顾客及对应订单，箱池和优化上界同步改为7，重新计算输入可证的K候选域。部分小规模仓—时段单元会变空，实际需求分布如实记录，每个外部时段总需求仍非空。economic.json逐字节保留，不据此评价经济优势。模型1—4保持距离目标，模型5—8保持现有成本目标；M3的显式成本对照须另建一致费用的实验配置。输入可行性以新版validation中的独立两周期见证为准；见证不自动作为ALNS初解。需求密度、仓—时段数量和来源见[设计总表](data/formal_benchmark27_spacetime_v4_1/design_summary.csv)。

费用包含合成情景和有来源说明的参考参数，各自适用范围见[费用复核](docs/cost_calibration_review_20261006.md)。真实配送基础不等于完整真实闭环数据，已保存方案重计价也不等于按新目标重新求解。

当前计划与说明见[文档导航](docs/README.md)，已结束的逐轮开发记录见[历史归档](archive/docs/README.md)。正式报告随对应实验保存在 `output/`；复现方法见[开发指南](docs/development.md)。
