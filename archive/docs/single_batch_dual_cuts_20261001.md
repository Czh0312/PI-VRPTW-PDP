# 单批次 M1/M2：时间下界试验与分仓对偶约束

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Mode: run; user-authorized exact-algorithm implementation and frozen comparisons
- Verification Status: EXECUTED; independent route, schedule and assignment-partition oracles
- Scope: B=1 M1/M2; valid root relaxation and optional complete-MILP cuts

## 实现和采用范围

本轮先实现有限车辆的区间时间必要条件，再尝试将列生成的对偶信息转成完整 MILP 的分仓距离约束。
两个方向均为显式实验选项。生产模型的默认入口不变；M3/M4 不进入纯配送原型。
本轮没有实现完整分支定价，也不把受限路线池的最优状态作为原问题的最优证明。

`root_route_bound(..., time_cuts=True)` 在路线主问题中加入区间占用约束；
`solve_with_route_bound(..., dual_cuts=True)` 把不含时间惩罚的定价证书转成二级距离不等式。
两个选项不能同时启用，避免将含时间惩罚的对偶直接误用为距离系数。
`pool_time > 0` 保留上一轮实体车辆排程初解阶段；它的内部最优值和下界仍不进入全局证明。
所有阶段共享同一个总预算，包括建模、定价、初解排程和完整 MILP。

## 区间时间约束及退步记录

对每仓选择配送完成期限、配送截止和周期终点作为区间右端，选择部分窗口下界作为左端。
时间段 [a,b] 内，路线 r 的 W(r,a,b) 是以下操作不可避免落入区间的最少时长之和：
装车准备及首段行驶、客户服务、内部行驶、末段行驶及回仓卸货和终端周转。

一个连续操作长 L，可能起始时刻 t 属于 [l,u]，其与区间 [a,b] 的最小重叠为

    max(0, min(L, b-a, l+L-a, b-u))

纯配送日程可在保持客户服务时刻和距离不变的情况下提前内部行驶及末段回仓。
因此上述连续操作可投影为同辆车互不重叠的操作。若一级路线 t 在时刻 supply(t,s) 完成仓 s 到货，
有 V 辆二级车，则以下是原模型的必要条件：

    Σ_r W(r,a,b) λ_r <= Σ_t V max(0, b-max(a,supply(t,s))) α_t

一级列使用最早到货，只会放宽右端。未充计的等待和车辆衔接继续被放松。
约束按周期长度缩放；定价目标增加非负时间惩罚，并在标签和 MILP 两个定价器中保持一致。
完整保留 1/2 任务的所有可行次序，避免带时间惩罚时仅保留最短距离次序造成错误修正。
未完成定价仍使用有效下界修正，不能使用受限 LP 值直接剪枝。

`output/experiments/single_batch_time_20261001/` 保留全部探索和退步：

- `root_20_probe.json`：早期前缀版本的探索记录，不是冻结的同预算比较。
- `root_20_interval_probe.json`：区间版本 20 客户下界 498.4961，松弛值 498.5070。
- `root_30_interval_probe.json`：30 客户下界 609.4555，松弛值 609.4738，与旧松弛最优值相同。
  该探针未冻结，不能据其运行时间宣称加速。
- `final_short/`：20 客户、种子 42/43、总预算 30 秒、根节点上限 10 秒、路线池上限 3 秒。
  原流程距离均为 536.3556，Gap 均为 7.3293%；时间约束流程距离均为 558.1857，Gap 均约 10.6935%。
  下界仅从 497.0444 提高到 498.4961，不能弥补可行解退步。
- `enriched_short/`：在私有初解池中增加按原始距离生成的候选路线。两次各增加 5 条路线，
  时间约束流程仍为 558.1857；原流程分别为 543.0707 / 536.3556。
  新路线只用于初解发现，不改变已求解 LP 和下界证书。此改动没有恢复质量，记录完整保留。

时间约束在微型例子中能识别趟数松弛漏掉的实体车辆不可行性，但当前有限预算对照出现退步，
因此没有将该选项加入默认流程，也没有继续用更大计时批次追逐正结果。

## 分仓对偶距离约束

`single_batch_dual_cuts.py` 使用每仓覆盖对偶 π、非正趟数对偶 μ 和经修正的定价下界。
不含时间惩罚时，路线的约化费用是

    rc_s(r) = distance(r) - Σ_{i in r} π_si - μ_s

定价下界 δ_s 和完整 1/2 任务目录给出的 β_s 保证

    rc_s(r) >= min(0, δ_s)
    rc_s(r) >= β_s |r|

对实际路线求和，令 z_cs 表示客户 c 分配仓 s，N_s 是仓 s 实际使用的趟数，
得到两种全局有效不等式：

    E2_distance >= Σ_{s,i} π_si z_customer(i),s
                   + Σ_s (μ_s + min(0,δ_s)) N_s - margin

    E2_distance >= Σ_{s,i} (π_si + β_s) z_customer(i),s
                   + Σ_s μ_s N_s - margin

同一客户多个订单的系数合并到一个分仓变量。M1 的分仓取输入常数。
使用实际趟数变量保留原模型信息，未把趟数上限或初解阶段的两趟限制写入原模型。
每轮定价生成两种平面，最多转移最近 16 条；未完成定价也必须先作保守修正。
这些约束随分仓分支变化，比一个常数总距离下界表达更多信息。

完整 MILP 用一个二级距离变量汇总原始有向弧成本，再添加上述平面。
压缩模型的内部弧跨车辆、趟次和仓点共用，按变量身份只计一次；边界弧保持各自成本。
证书绑定原问题 SHA256，检查模型、订单、政策、车队、服务和矩阵，拒绝其他实例。
整数最优性仍由完整 MILP 证明；LP 收敛不代表原问题最优。

## 冻结同预算对照

结果目录 `output/experiments/single_batch_dual_20261001/`。
每对使用相同冻结输入、已审计初解、种子、预算和单线程配置，顺序独立进程运行，
种子 43 反转方法顺序。没有失败重试；输入、源码、参数、日志、结果和审计全部保存。
基线为上一轮列生成、路线池排程、常数下界和完整联合 MILP 的混合流程。
新增方法保持同一根节点和初解算法，仅多转移分仓对偶约束。

| 规模 / 总预算 / 种子 | 原流程距离 | 对偶约束距离 | 原下界 | 新下界 | 原 Gap | 新 Gap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 20 / 30 秒 / 42 | 536.3556 | 535.2960 | 497.0444 | 502.6851 | 7.3293% | 6.0921% |
| 20 / 30 秒 / 43 | 543.0707 | 535.2960 | 497.0444 | 502.6851 | 8.4752% | 6.0921% |
| 30 / 60 秒 / 42 | 687.4789 | 676.9873 | 563.1347 | 571.2667 | 18.0870% | 15.6163% |
| 30 / 60 秒 / 43 | 676.9873 | 676.9873 | 602.3340 | 571.5846 | 11.0273% | 15.5694% |

20 客户四次均完成同一个根节点松弛，具有相同的 227 条路线、根节点下界和 543.0707 的路线池初解。
因此，两个种子的改善与转移到完整模型的对偶约束一致。
30 客户根节点均耗尽 30 秒上限，但生成的路线池和最后完成的定价轮次不同。
原流程 seed 43 得到更好的定价修正下界 602.3340，新增方法的根节点下界仅为 563.1347，
导致最终 Gap 退步。该轮次不能据 seed 42 的改善宣称大实例稳定加速。

为区分根节点进度和完整 MILP 的影响，追加 `fixed_handoff_long/`，两种方法共享正式
`columns_dual_42` 的证书与已审计结果作为初解，仅改变是否导入 16 条对偶约束。
两个种子的完整 MILP 各分配 30 秒，包括建模；该补充预算不含此前证书计算和初解发现，
不能与正式 60 秒端到端结果当作同一种预算口径比较。

| 种子 / MILP 阶段预算 | 原距离 | 对偶约束距离 | 原下界 | 新下界 | 原 Gap | 新 Gap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 42 / 30 秒 | 676.7387 | 676.7387 | 563.1347 | 571.3102 | 16.7870% | 15.5789% |
| 43 / 30 秒 | 675.3819 | 676.9873 | 563.1347 | 580.7053 | 16.6198% | 14.2221% |

固定交接后，两个种子的下界和 Gap 均改善；seed 43 的距离则退步约 0.24%。
这支持对偶约束有助于完整模型下界，但不能由此声称可行解一定更好，或已经加快全局最优证明。
正式端到端的 Gap 为 3 改善 / 1 退步；补充为 2 改善 / 0 退步。
本轮时间约束和对偶约束正式、补充共 20 个结果全部通过独立审计与两周期重放，原问题最优证明数为 0。

正式短/长批次的源码全集 SHA256 一致。固定交接批次新增运行器选项，另外有三处其他聊天的源码变更：
ALNS `neighborhoods.py`、`multibatch_e1_inventory.py`、`multibatch_route_pool.py`。
这些模块不在本次单批次完整 MILP 及其独立初解构造路径中执行，未回退其变更。
每个批次内部两方法始终使用同一冻结源码。完整差异列表和结果哈希见 `summary.json` 和 `result_manifest.json`。

## 当前决策

分仓对偶约束作为 M1/M2 的可选实验入口保留；时间约束也仅供明确的研究试验使用。
不修改默认精确求解策略，不推广到含回收的 M3/M4。
20 客户的同根节点对照支持继续研究对偶约束；30 客户端到端效果受时限定价进度影响，仍不稳定。
下一步需要提高根节点定价的稳定性并扩大样本，才有依据决定是否默认启用或开发完整分支定价。

## 验证与复现

独立检查包含：连续操作起始端点枚举、每个整数发车时刻的实际占用、两种加权定价与完整路线
排列对照、未完成定价下界、完整枚举 LP、原模型整数最优值、重复地址、零时长、原模型不可行性、
私有初解池隔离，以及每种分仓的全部路线拆分而非仅最优解。
新增二级距离定义在独立、共享和压缩三种弧结构下均核对有向费用，混合流程仍能证明微型原模型最优。
另枚举多辆车、多趟、每个整数到货/发车时刻和不同等待窗口的完整微型日程，验证时间必要条件的投影。
专项检查 **288 passed in 1.76s**；全库 **1343 passed, 14 warnings in 56.56s**。
14 条警告均为既有 Matplotlib/Pyparsing 弃用提示。全库验证前后记录的 34 个源码和专项测试文件 SHA256 无变化。
原始日志、专项测试快照和验证哈希保存在结果目录的 `targeted_tests.log`、`full_tests.log`、
`tests_snapshot/`、`validation.json` 及 `full_tests_source_before/after.json`。
计时实验均顺序执行，未与本聊天的求解测试重叠。验证程序逐个重审计结果并核对冻结输入和源码哈希。

```python
from models.routing.solvers.exact.single_batch_columns import solve_with_route_bound

result = solve_with_route_bound(problem, time_limit=60, root_time=30,
    pool_time=5, pricing_method='mip', dual_cuts=True, initial_result=audited_start)
```

```sh
.venv/bin/python experiments/runs/run_single_batch_columns.py \
  --frozen-runs output/experiments/single_batch_site_flow_20261001/final/zhou_Set2_100-5-1b_subset20_M2 \
  --methods columns_pool columns_dual --seeds 42 43 \
  --total-time 30 --root-time 10 --pool-time 3 --pricing-method mip \
  --output output/experiments/single_batch_dual_new_short

.venv/bin/python experiments/runs/run_single_batch_columns.py \
  --frozen-runs output/experiments/single_batch_site_flow_20261001/final/zhou_Set2_100-5-1b_subset30_M2 \
  --methods columns_pool columns_dual --seeds 42 43 --total-time 30 \
  --handoff-result output/experiments/single_batch_dual_20261001/final_long/zhou_Set2_100-5-1b_subset30_M2/columns_dual_42/result.json \
  --output output/experiments/single_batch_dual_new_handoff

.venv/bin/python -m pytest tests/routing/test_single_batch_columns.py \
  tests/routing/test_single_batch_route_pool.py tests/routing/test_single_batch_time_budget.py \
  tests/routing/test_single_batch_dual_cuts.py -q
```
