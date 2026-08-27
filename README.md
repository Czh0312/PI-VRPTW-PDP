# 2E-VRPTW-PDD

两级带时间窗取送货车辆路径问题求解框架，包含 Model 1–7、Gurobi MIP 与 ALNS 启发式求解器。

当前统一编号及旧结果换算见
[`docs/model_numbering_migration.md`](docs/model_numbering_migration.md)。

当前唯一正式数据集是 [`data/zhou_later_pickup/`](data/zhou_later_pickup/)：
`small` 包含 7/10/12 客户实例，`large` 包含 100/200 客户实例。旧 Solomon
派生数据和北京数据已从工作区删除；依赖 C101/R101 等旧路径的实验脚本仅作历史代码。

## 精确求解口径

项目将“有可行路线”和“已证明全局最优”严格分开：只有求解器返回
`proven_optimal=True`（完整模型为 `OPTIMAL`，或有效下界证书与可行上界闭合）时，
结果才能称为精确解。达到时间限制的 incumbent 会保留目标值、下界和 gap，
但会标记为 `FEASIBLE_NOT_PROVEN`，不会进入 `run_comparison.py` 的 Exact 均值
或成功率。

- Model 1 / Model 3：`solve_exact(...)` 默认只返回已证明最优的解；如需限时
  incumbent，显式调用 `solve_mip(...)`。
- Model 5：定义为 **Model 3 + E2 空容器单次车辆间接力**，路线、携带车、
  交接点和接收车可在一个 MIP 中联合精确优化。默认先用保留原 pickup 访问和时间窗、
  仅放松回收载荷的模型构造严格下界；该下界与可行解相等时即可认证完整候选空间，
  否则回退联合 MIP。有限近邻候选使用
  `solve_restricted_mip(...)`，不能作为原模型的最优性证明。详见
  [`docs/model5_exact_container_relay.md`](docs/model5_exact_container_relay.md)。
- Model 6：采用**同质空箱 O/R 双商品弧流 + 服务地点异步交接**的紧凑联合 MIP。
  路线、服务车辆和整数交接量同时优化；只有原生回收流 `own` 能转出，接力流
  `relay` 不能再次中转。默认先尝试严格的零回收载荷松弛证书；证书不闭合时回退
  完整紧凑MIP。只有有效下界与可行上界闭合，或完整MIP以零gap返回 `OPTIMAL` 时，
  才标记 `proven_optimal=True`。详见
  [`docs/model6_compact_homogeneous_relay.md`](docs/model6_compact_homogeneous_relay.md)。

Model 3 另提供 `models.model_3.route_universe.solve_route_universe_exact`：它在
每个卫星任务数不超过 10 时穷举全部容量/时间窗可行 E2 路线，并把路线选择与
原始 E1–E2 联合时间约束同时求解。只有路线宇宙完整且 MIP 返回 `OPTIMAL` 才
认证；任务或路线数超过安全上限时直接报告 `ROUTE_UNIVERSE_INCOMPLETE`，不会
退化成“伪精确”的受限列池。

`models.model_3.route_master.solve_route_master_exact` 是对应的直接路线主问题：
每条 E2 路线都保留自己的发车/返回时间，客户保留唯一的绝对服务时刻，因此 E1
送达、所有 E2 车返回后回收、以及跨车的“先送后取”都仍在同一个 MIP 中。它是后续
branch-price 的正确主问题；当前仅在完整路线宇宙时认证。

同文件的 `solve_route_master_pool(...)` 接受 ALNS 或既有 MIP 路线作为受限池，
用于后续的列生成迭代；它即使返回 `OPTIMAL` 也固定标为
`restricted_route_pool_optimal`，绝不等同于全局最优。

路线主问题还支持 Ryan–Foster `same` / `different` 任务对分支。单个分支节点
即使已被 MIP 闭合，也标为 `branch_node_optimal`；只有完整分支树闭合才可以形成
全局精确证明。

[`models/model_3/route_pricing.py`](models/model_3/route_pricing.py) 提供单条
E2 路线的精确定价 oracle，支持任意弧成本、任务奖励和 Ryan–Foster 分支规则。
它只认证“该单路线子问题”的最优性；在联合主问题对偶及完整分支树接入前，不会
输出 Model 3 全局最优结论。

精确求解的模块职责、返回状态、回归命令与尚未完成的 branch-price-and-cut 工作，
见 [`docs/exact_solver_architecture.md`](docs/exact_solver_architecture.md)。

## 精确求解结果（已证明）

**口径（2026-08-10 收紧）**：`proven_optimal` 只授予 **Gurobi 对完整未缩减模型直接求解
返回 `OPTIMAL`** 的结果。任何基于自定义证书/分解/严格闭合定理的结论都不再计入
“已证明”表——它们只是研究性证据，见
[`reference/exact_proof_evidence/`](reference/exact_proof_evidence/)。

| 算例 | 结果 | 状态 |
|---|---:|---|
| Model 1 `100_customers/C101` | 1622.92 | **不可复现**：2026-08-10 直接求解 1800s 找到更优可行解 1602.22、界 1166.95、gap 27.2%，未证明；旧数值来源待查证 |
| Model 3 `10_customers/C101` | **543.09** | proven_optimal（Gurobi 直接 OPTIMAL） |
| Model 3 `50_customers/C101` | **1289.06** | 曾报告 Gurobi OPTIMAL（下界注入+warm start 后 20.9s 闭合）；**2026-08-10 复现：同样输入 600s 只到 gap 0.18%（界 1289.056），需复核** |
| Model 3 `100_customers/C101` | 1829.0094 | **非直接求解认证**：Gurobi 直接求解 600s 无可行解、下界 1657.70；1829.0094 来自 ALNS + 证书路径 |
| Model 6 `seed=105`（4 客户机制实例） | **95.556056** | 证书未闭合后回退完整MIP，12.138s达到 `OPTIMAL`；约比Model 5快74.6倍 |

5/10/15/20 客户小规模算例均可在秒级直接证明最优，这里只列大规模的代表结果。

### Model 3 直接求解的实测限制（2026-08-10）

直接交给 Gurobi 求解弧流 MIP 的可达范围（2026-08-10）：

- `100_customers/C101`（Model 1）：1800s → incumbent 1602.22、界 1166.95、gap 27.2%，未证明。
- `50_customers/C101`：无辅助裸跑 900s → 无可行解、下界 1232.68；
  注入卫星下界 + warm start 后 600s → incumbent 1291.40、界 1289.056、gap 0.18%，**未闭合**。
- `100_customers/C101`：600s → 无可行解、下界 1657.70，**无法直接闭合**。
- 路线主问题路线宇宙在 100c 下实测约 **1190 万条**（S1 单卫星 423 万条），
  全宇宙直接建 MIP 不现实（千万级二进制列）。

因此 100c C101 的 1829.0094 目前只能通过**证书研究路径**支持（E1 阶跃函数 +
S1 floor 精确定价证书，`experiments/probe_e1_stepfn.py` +
`experiments/probe_s1_floor_cert.py`），该结论不再标为 `proven_optimal`。

## 当前重点

当前主线是 **Model 7-Hybrid-Safety-Optimized-V6**：

```text
每轮 ALNS candidate 内嵌受控中转试探
+ strong initial 强可行初始解
+ compact transshipment portfolio
+ legacy initial safety 分支
+ risk-aware / exhaustive sparse / prefix beam 补充分支
+ V3 跨车重排最终精修
+ V4 任务对交换补充分支
+ V5 迭代跨车重排/交换分支
+ V6 Full 稳定增强分支（所有规模默认运行）
```

完整实验总结见：

```text
docs/model7_optimization_summary.md
```

## Model 4：顾客-前置仓分配内生化（自由分配）

Model 4 与 Model 3 的**唯一区别**：顾客归属哪个前置仓是决策变量（z_cs），
不再由数据里的 `assigned_sat` 固定。其余机制（E2 每仓独立车队、路线锚定本仓、
单分配送收同仓、E1 车队/容量/时间窗衔接、先送后取）与 M3 完全一致。
因此 M4 的解空间是 M3 的严格超集——任何成本差都归因于"自由分配"这一个自由度。

实现：

```text
models/model_4/alns_solver.py   分配感知 ALNS（跨仓搬移 + 动态 E1 需求重建）
models/model_4/exact.py         小规模精确 MIP（5/10 客户可证明最优）
experiments/run_model4_vs_model3.py   M4 vs M3 对比脚本（纯时间预算）
docs/model4_free_assignment.md  建模要点与运行说明
```

关键结果（2026-08-11，30 算例 × 500 迭代 / 60s，seed=42）：

| 规模 | M4更好 | 持平 | M4更差 | M3不可行但M4可行 | 平均gap% |
|---|---:|---:|---:|---:|---:|
| 5_customers | 2 | 4 | 0 | 0 | -1.39% |
| 10_customers | 6 | 0 | 0 | 0 | -7.15% |
| 20_customers | 5 | 0 | 0 | 1 | -6.24% |
| 50_customers | 5 | 0 | 0 | 1 | -9.10% |
| 100_customers | 5 | 0 | 0 | 1 | -9.51% |

- **30/30 算例 M4 不劣于 M3，0 更差**（M4 早期在 50c RC201 落后，根因是
  M4 缺少 M3 一直有的 E1 局部搜索，补上 E1 2-opt 后 RC201 变为 5/5 种子全胜）；
- **恢复可行性**：20c RC101、50c RC101、100c R101 固定分配下不可行，
  M4 均找到可行解（分别搬移 6/11/27 个顾客）；
- 精确验证：5c/10c C101 的 M4 精确解 = 391.57 / 543.09，与 M3 已证明最优一致；
- ⚠️ 10c 全为单前置仓算例，-7.15% 全部来自修复算子差异（成对联合插入），
  与自由分配无关；20c 以上的收益才是自由分配的贡献。
- 回收时间语义修正：E1 回收下界改为动态的"该仓最后一个容器返回时刻"
  （MIP `Link_Pickup` / ALNS 等待逻辑），不再用 9999 大数占位，详见
  [docs/model4_free_assignment.md](docs/model4_free_assignment.md)。

## 认证精确分解（Model 3，100 顾客规模）— 证书路径，不计入直接精确解

**注意**：本节是证书研究路径。它的 `PROVEN_OPTIMAL` 判决**不满足**“Gurobi 直接求解
完整模型 `OPTIMAL`”的口径，不计入 README 上方的“已证明结果”表。

`models/model_3/exact_decomposition.py` + `experiments/run_exact_m3_decomposition.py`
实现了 Model 3 的可认证精确分解管线，把 C101 的"严格闭合定理"推广为通用算法
（研究用途）：

```text
min over 可行 E1 计划 P  [ E1_cost(P) + sum_s E2_s(tau_s(P)) ]
```

1. **每卫星下界**：E2_s(tau) >= L_s 对任何 E1 计划成立（floor/cap 只会收缩可行
   路线族），故 E1_cost >= UB - sum L_s 的计划自动闭合。
2. **E1 帕累托前沿**：枚举成本低于阈值 C0 的 E1 计划（no-good 循环，断点续跑），
   归并 tau 类并过滤被支配类。
3. **floor 受限证书**：对开放类只需每个卫星一个 `s_max >= f_min(s)` 受限列生成
   证书（单调性使同一证书覆盖所有开放类）。

CLI：

```bash
# C101（全部缓存，~3 分钟出证）
.venv/bin/python experiments/run_exact_m3_decomposition.py \
    --scale 100_customers --instance C101 \
    --columns-json reference/exact_proof_evidence/merged_columns/100_customers__C101__columns.json \
    --satellite-lb-json reference/exact_proof_evidence/exact_incumbents/m3_100_c101_satellite_bcp_exact_lbs.json \
    --incumbent 1829.0093866572092 --total-time-limit 3600 \
    --output-dir output/exact_m3_decomp
```

证书管线已出证：`100_customers/C101`（1829.0094，PROVEN_OPTIMAL，191s）与
`20_customers/C101`（全流程现场生成，3s）。该判决按上节口径**不**记为
`proven_optimal`。`--total-time-limit` 保证单次运行到点自动收尾并写判决；
`--resume-frontier-json` 支持前沿枚举断点续跑（每次运行会保存
`frontier_plans.json`）。

C103 的 8 个卫星下界已全部认证（S0=168.8318 由 BCP 证明最优；S1/S3/S6 用长定价
列生成认证 LP；S2/S4/S5/S7 已认证），UB=1805.5435 已由路线池主问题给出；
当前瓶颈在 C0=847 下的 E1 前沿枚举与 S1/S3/S6 的 pricing 认证墙（与 C101 历史上
同一难点，需要 exact-label pricing / 更强的 BCP 调参），相关中间产物全部缓存在
`output/exact_m3_decomp/100_customers__C103/`。

### exact-label 定价支持 SRI 割对偶（2026-08-07）

`e2_route_cg._solve_pricing_labeling_exact` 新增 `cut_duals` 参数：SRI 割的路由
系数 `floor(|route ∩ cut| / 2)` 在标签选中第 2/4/... 个成员时增量累加割对偶，
由于系数是 visited 掩码的确定函数，same-visited 支配仍然有效。`run_satellite_bcp.py`
已允许 exact-label 处理割（移除 `CUTS_REQUIRE_MIP_PRICING` 拦截）。

验证：C103 S2（18 任务）在 99-125 条 SRI 割下根 LP 从 113.27 抬到 **117.49**
（+4.22），exact-label 多次 `EXACT_LABEL_OPTIMAL` 穷尽认证；36 任务大卫星
（S1/S3/S6）的标签空间过大，仍需长尾调参。这打通了"小卫星割平面下界认证"
的代码路径，是 C101 项目缺失能力的补全。

## 历史完整公平对比（Full V6，旧数据）

该节使用迁移前的 Solomon 派生数据。原数据已删除，结果已归入
`archive/legacy_results_pre_renumbering/`，不能与当前 Zhou 数据结果合并统计。

实验设置：

```text
iterations = 60
time = 6 per solver/variant
seed = 42
transshipment_time_budget = 0.02
Model 3/5/6 strong initial = True
Model 7 portfolio = optimized_v6 (Full V6)
```

说明：这是当前推荐口径的完整公平对比。Model 3/5/6 同样启用 strong initial，Model 7 使用 Full V6 portfolio 取最优；计算量高于单次 ALNS，结果主要用于比较解质量。

当前实测结果：

| 模型 | 可行数 | 失败数 | 比强 Model 3 更好 | 持平 | 更差 | M3无解但可行 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|---:|
| Model 3 + strong initial | 118 | 2 | 0 | 118 | 0 | 0 | 0.00% |
| Model 5 + strong initial | 118 | 2 | 35 | 49 | 34 | 0 | 0.42% |
| Model 6 + strong initial | 118 | 2 | 18 | 100 | 0 | 0 | -0.03% |
| Model 7-Hybrid-Safety-Optimized-V6 | 118 | 2 | 92 | 26 | 0 | 0 | -3.91% |

分规模结果：

| 规模 | M7 更好 | M7 持平 | M7 更差 | 均失败 | M7 平均 gap% |
|---|---:|---:|---:|---:|---:|
| 5_customers | 6 | 24 | 0 | 0 | -0.99% |
| 10_customers | 28 | 2 | 0 | 0 | -5.05% |
| 50_customers | 29 | 0 | 0 | 1 | -4.54% |
| 100_customers | 29 | 0 | 0 | 1 | -5.11% |

完整输出：

```text
output/experiments/model3567_all_nonbeijing_120datasets_optimized_v6_iter60_seed42_strong_baselines.md
output/experiments/model3567_all_nonbeijing_120datasets_optimized_v6_iter60_seed42_strong_baselines.csv
```

结论：在统一强初始解后，当前 Full V6 M7 相比强 Model 3 做到 `92 更好 / 26 持平 / 0 更差`，平均成本下降 `3.91%`。两个均失败算例是 `100_customers_R101` 和 `50_customers_RC101`。

## 历史对照：Hybrid-Safety

为了拆分“strong initial 贡献”和“Model 7 中转机制贡献”，也测试了让 Model 3/5/6 同样使用 strong initial 的版本：

| 模型 | 可行数 | 失败数 | 比强 M3 更好 | 持平 | 更差 | M3无解但可行 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|---:|
| Model 3 + strong initial | 118 | 2 | 0 | 118 | 0 | 0 | 0.00% |
| Model 5 + strong initial | 118 | 2 | 23 | 47 | 48 | 0 | 0.82% |
| Model 6 + strong initial | 118 | 2 | 13 | 105 | 0 | 0 | -0.01% |
| Model 7-Hybrid-Safety | 118 | 2 | 82 | 36 | 0 | 0 | -2.53% |

结论：strong initial 本身把 Model 3 从 `102/120` 可行提升到 `118/120` 可行，因此原始对比里的部分优势确实来自初始解；但在统一强初始解后，Model 7 仍然做到 `82 更好 / 36 持平 / 0 更差`，说明中转迭代与 safety portfolio 仍有稳定增量收益。

```text
output/experiments/model3567_all_nonbeijing_120datasets_hybrid_safety_iter80_seed42_strong_baselines.md
output/experiments/model3567_all_nonbeijing_120datasets_hybrid_safety_iter80_seed42_strong_baselines.csv
```

## Model 7 继续优化分支

在当前 M7-Hybrid-Safety 上先后加入四层补充分支：

```text
V1:
risk_sparse_guard_T1S1
risk_nearest_guard_T5S3
exhaustive_sparse_guard_T1S1
prefix_beam_T2S1

V2:
stagnation_exhaustive_T1S1
stagnation_nearest_T5S3
stagnation_prefix_beam_T3S1
risk_refine_guard_T5S3

V3:
rebalance_guard_T1S1
risk_rebalance_guard_T5S3
stagnation_rebalance_T5S3

V4:
swap_rebalance_guard_T1S1
risk_swap_rebalance_T5S3

V5:
v5_fixed_rebalance_T1S1
v5_stag_rebalance_T1S1
v5_stag_swap_T1S1

V6:
v6_fixed_rebalance_T2S1
v6_stag_swap_T2S1
v6_cand_refine_rebalance_T1S1
v6_risk_cand_rebalance_T5S3
```

其中 V3 是当前最值得保留的新增方向：在 ALNS 和中转搜索结束后，对同一卫星内不同 E2 路线进行顾客任务对搬移，只有全局可行且总成本下降才接受。V4 在 V3 基础上尝试两条路线间交换两个完整顾客任务对，适合作为额外候选分支，不建议替代 V3。

V5 的新增点是把“跨车重排/交换”也放进 ALNS 迭代中的 candidate 试探：每轮 candidate 先完成受控中转，再按固定间隔或搜索停滞触发一次小范围跨车重排。V6 在 V5 基础上替换为稳定增强 portfolio：增加 T2S1 重排/交换、候选中转后路线精修、risk-aware candidate refine + rebalance，并保留 V5 作为兜底，因此不会比 V5 更差。当前标准对比脚本统一采用 Full V6：所有规模都运行 4 个 V6 extra 分支，避免实验口径分裂。

统一强初始解下，`5/10_customers` 60 个小规模算例 + `50/100_customers` 24 个代表样本合计 84 个非北京算例：

| 对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Model 5 vs Model 3 | 15 | 45 | 22 | 2 | 0.43% |
| Current M7 vs Model 3 | 48 | 34 | 0 | 2 | -2.46% |
| Optimized V1 vs Model 3 | 49 | 33 | 0 | 2 | -2.64% |
| Optimized V2 vs Model 3 | 53 | 29 | 0 | 2 | -2.77% |
| Optimized V3 vs Model 3 | 56 | 26 | 0 | 2 | -3.26% |
| Optimized V4 vs Model 3 | 56 | 26 | 0 | 2 | -3.33% |
| Optimized V5 vs Model 3 | 56 | 26 | 0 | 2 | -3.44% |
| Stable V6 vs Model 3 | 56 | 26 | 0 | 2 | -3.49% |
| V3 vs V2 | 19 | 63 | 0 | 2 | -0.53% |
| V4 vs V3 | 3 | 79 | 0 | 2 | -0.06% |
| V5 vs V4 | 8 | 74 | 0 | 2 | -0.14% |
| V6 vs V5 | 8 | 74 | 0 | 2 | -0.06% |

结论：V3 跨车重排是最强的单次跃升；V4 交换有局部价值；V5 迭代重排/交换稳定释放中转后的路线均衡收益；V6 在 50/100 客户代表样本上继续增加 8 个赢点，在 5/10 客户上保持持平。当前标准优化版为 Full V6 portfolio，并在跨车重排中加入距离收益剪枝以减少无效完整可行性检查。

输出：

```text
output/experiments/model7_v6_stable_comparison_60datasets_iter60_seed42_strong_baselines_small_5_10_v6.md
output/experiments/model7_v6_stable_comparison_24datasets_iter40_seed42_strong_baselines_large_sample_50_100_v6.md
output/experiments/model7_v6_stable_comparison_84datasets_seed42_combined.md
```

## 常用命令

运行主程序：

```bash
python main.py
```

全部非北京算例对比：

当前小算例精确实验入口：

```bash
python experiments/run_mainline_small_exact.py \
  --groups small --scenarios standard_same_day \
  --models 1 2 3 4 --m4-formulation arc_pruning
```

## 目录

```text
core/          实例、节点、车队数据结构
models/        Model 1–7 和 ALNS 公共组件
experiments/   批量实验与对比脚本
data/          Zhou 正向两级配送 + 延后包装回收数据集
docs/          当前实验总结文档
memory/        研究过程记录
output/        实验输出
results/       当前 Zhou 数据的机器可读求解结果
archive/       旧编号、旧数据实验结果归档
```
