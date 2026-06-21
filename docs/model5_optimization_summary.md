# Model 5 优化尝试与实验总结

本文档汇总当前 Model 5 多轮优化尝试、各方法效果、与 Model 2/3/4 的对比，以及针对 Model 5 更差情况的修复实验。所有实验均不使用北京算例。

## 0. 最新完整公平对比（Full V6）

数据：`5/10/50/100_customers` 全部非北京算例，共 120 个。

实验设置：

```text
iterations = 60
time = 6 per solver/variant
seed = 42
transshipment_time_budget = 0.02
Model 2/3/4 strong initial = True
Model 5 portfolio = optimized_v6 (Full V6)
```

总体结果：

| 模型 | 可行数 | 失败数 | 比强 Model 2 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|---:|
| Model 2 + strong initial | 118 | 2 | 0 | 118 | 0 | 2 | 0.00% |
| Model 3 + strong initial | 118 | 2 | 35 | 49 | 34 | 2 | 0.42% |
| Model 4 + strong initial | 118 | 2 | 18 | 100 | 0 | 2 | -0.03% |
| Model 5-Hybrid-Safety-Optimized-V6 | 118 | 2 | 92 | 26 | 0 | 2 | -3.91% |

分规模看 Model 5：

| 规模 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| 5_customers | 6 | 24 | 0 | 0 | -0.99% |
| 10_customers | 28 | 2 | 0 | 0 | -5.05% |
| 50_customers | 29 | 0 | 0 | 1 | -4.54% |
| 100_customers | 29 | 0 | 0 | 1 | -5.11% |

分类型看 Model 5：

| 类型 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| C | 32 | 8 | 0 | 0 | -5.12% |
| R | 29 | 10 | 0 | 1 | -2.80% |
| RC | 31 | 8 | 0 | 1 | -3.77% |

输出：

```text
output/experiments/model2345_all_nonbeijing_120datasets_optimized_v6_iter60_seed42_strong_baselines.md
output/experiments/model2345_all_nonbeijing_120datasets_optimized_v6_iter60_seed42_strong_baselines.csv
```

结论：当前 Full V6 M5 在完整公平口径下没有出现劣于强 Model 2 的算例；主要收益集中在 10/50/100 客户，5 客户大多持平。Model 3 有较多波动，Model 4 稳定不劣但改善幅度很小，Model 5 是当前最有效的版本。

## 1. Model 5 的核心定义

Model 5 的核心思想是：

```text
每次 ALNS 生成 candidate 后，先在 candidate 内部做受控中转试探；
中转后的完整 candidate 再交给 ALNS 接受准则，决定是否进入下一轮。
```

与 Model 3/4 的区别：

| 模型 | 中转触发位置 | 特点 |
|---|---|---|
| Model 3 | ALNS 迭代中作为普通中转算子 | 中转与路线协同，但容易累积扰动 |
| Model 4 | ALNS 求完后做中转后处理 | 稳定，通常不劣于 Model 2，但协同性弱 |
| Model 5 | 每个 ALNS candidate 内嵌中转试探 | 兼顾协同搜索与受控接受 |

## 2. 已尝试方法

### 2.1 中转深度 H

问题：每轮 ALNS 后只做 1 次中转是否太弱？

尝试方案：

| 方法 | 含义 |
|---|---|
| H=1 | 每个 candidate 最多做 1 次中转 |
| H=2/H=3 | 每个 candidate 连续做多次中转 |
| best-prefix | 多次中转前缀中保留最好前缀 |
| decreasing | 前期多次，后期减少 |

代表结果：

| 算例 | Model 2 | M5 H=1 | M5 H=2 | M5 H=3 | 结论 |
|---|---:|---:|---:|---:|---|
| 10 C101 | 591.38 | 601.20 | 599.49 | 599.49 | H=2略好于H=1，但仍差于M2 |
| 10 R101 | 596.16 | 603.84 | 610.37 | 610.37 | H越多越差 |
| 10 RC101 | 665.22 | 635.35 | 662.48 | 662.48 | H=1最好 |
| 50 C101 | 1389.50 | 1518.29 | 1616.38 | 1686.79 | H越多越差 |

结论：**H=1 最稳**。继续增加中转次数不是主方向。

### 2.2 中转选择策略

尝试方案：

| 策略 | 含义 |
|---|---|
| greedy | 每次选当前最佳中转 |
| range_random | 在接近最优收益的候选中随机选择 |
| beam | 保留多个中转候选路径 |
| conservative | 只接受强收益中转 |

代表结果：

| 算例 | Model 2 | Model 3 | Model 4 | 最佳 M5 | 最优来源 |
|---|---:|---:|---:|---:|---|
| 10 R103 | 546.10 | 546.72 | 546.10 | 546.03 | M5 range_random |
| 10 RC101 | 665.22 | 692.39 | 665.22 | 635.35 | M5 greedy/conservative |
| 10 RC103 | 683.38 | 757.77 | 683.38 | 674.87 | M5 beam |
| 50 C101 | 1389.50 | 1458.49 | 1389.50 | 1503.03 | Model 2/4 更稳 |

结论：没有单一策略普适最优，Model 5 必须用 portfolio。

### 2.3 pruning、质量门控、最终精修

尝试方案：

| 方法 | 作用 |
|---|---|
| transshipment_prune | 撤销低贡献中转 |
| quality_gate | 只对质量足够好的 candidate 做中转 |
| final_refine | 搜索结束后再做中转精修 |
| route_local_refine | 最终路径顺序局部调整 |

代表结果：

| 算例 | 基础 M5 | 改进后最佳 M5 | Model 2 | 结论 |
|---|---:|---:|---:|---|
| 10 R103 | 546.03 | 545.08 | 546.10 | final_refine 有效 |
| 50 C101 | 1518.29 | 1454.77 | 1389.50 | gate/prune 有改善，但仍不如M2 |
| 10 RC103 | 674.87 | 683.38 | 683.38 | 改进项不总是有效 |

结论：这些方法有局部收益，但不能单独解决稳定性。

### 2.4 baseline guard 与早期 portfolio

方法：

```text
先跑无中转基线；
再运行中转嵌入搜索；
若中转结果差于内部基线，则回退。
```

40 个非北京算例结果：

| 方案 | 算例数 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| 早期 Model 5 portfolio | 40 | 18 | 22 | 0 | -1.12% |

结论：baseline guard 是防劣化的关键，但只保护“内部基线”，不一定等于外部 Model 2 最优结果。

### 2.5 中转上限组合 Cap Portfolio

方法：同时测试多个中转上限。

| 记号 | max_total | max_per_satellite |
|---|---:|---:|
| T1/S1 | 1 | 1 |
| T2/S1 | 2 | 1 |
| T3/S2 | 3 | 2 |
| T5/S3 | 5 | 3 |

40 个非北京算例结果：

| 方案 | 算例数 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| 旧 portfolio T3/S2 | 40 | 18 | 22 | 0 | -1.03% |
| Cap portfolio | 40 | 28 | 12 | 0 | -1.91% |
| Cap-MS + seed7 | 40 | 30 | 10 | 0 | -2.06% |
| Cap-MS + seed7/99 | 40 | 33 | 7 | 0 | -2.14% |
| Cap-MS + nearest_k | 40 | 34 | 6 | 0 | -2.15% |

结论：多数算例偏好稀疏中转，T1/S1 最常成为赢家；少数算例仍需要 T5/S3。

### 2.6 更强可行初始解

方法：

```text
1. 先构造 E1 卫星配送；
2. 根据 E1 到达时间构造 E2 顾客取送货；
3. 再根据 E2 返回时间插入 E1 卫星回收；
4. 同时尝试成本型路线和紧迫卫星单独派车路线。
```

原失败集恢复：

| 初始解 | 原失败算例数 | M5恢复可行 | 仍失败 |
|---|---:|---:|---:|
| legacy initial | 12 | 0 | 12 |
| strong initial v1 | 12 | 7 | 5 |
| strong initial v2 | 12 | 11 | 1 |

50C 可比结果：

| 对比 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|
| legacy initial M5 vs Model 2 | 10 | 0 | 0 | -3.77% |
| strong initial M5 vs Model 2 | 10 | 0 | 0 | -7.22% |
| strong initial vs legacy initial | 10 | 0 | 0 | 平均成本 -52.68 |

结论：strong initial 是 Model 5 当前最重要的提升之一，尤其解决大规模 R/RC 的可行性入口问题。

## 3. 当前全量非北京实验

实验设置：

```text
数据：5/10/50/100_customers 全部 120 个非北京算例
iterations = 80
time = 10 per solver/variant
seed = 42
Model 5 = Hybrid-Safety portfolio
```

说明：Model 5-Hybrid-Safety 是多个候选分支取最优，计算量高于单次 Model 2 ALNS；本节主要比较解质量，不表示等墙钟时间公平竞赛。

输出：

```text
output/experiments/model2345_all_nonbeijing_120datasets_hybrid_safety_iter80_seed42.md
output/experiments/model2345_all_nonbeijing_120datasets_hybrid_safety_iter80_seed42.csv
```

总体结果：

| 模型 | 可行数 | 失败数 | 比M2更好 | 持平 | 更差 | M2无解但可行 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|---:|
| Model 2 | 102 | 18 | 0 | 102 | 0 | 0 | 0.00% |
| Model 3 | 102 | 18 | 17 | 22 | 63 | 0 | 3.54% |
| Model 4 | 102 | 18 | 22 | 80 | 0 | 0 | -0.04% |
| Model 5-Hybrid-Safety | 118 | 2 | 84 | 18 | 0 | 16 | -7.84% |

最佳模型次数：

| 最优模型 | 次数 |
|---|---:|
| Model 5 | 100 |
| Model 2 | 18 |
| 全部无解 | 2 |

分规模看 Model 5：

| 规模 | M5可行 | 更好 | 持平 | 更差 | 恢复M2无解 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|
| 5_customers | 30/30 | 16 | 14 | 0 | 0 | -6.47% |
| 10_customers | 30/30 | 26 | 4 | 0 | 0 | -5.44% |
| 50_customers | 29/30 | 23 | 0 | 0 | 6 | -7.99% |
| 100_customers | 29/30 | 19 | 0 | 0 | 10 | -13.58% |

与早期 strong compact 9配置对照：

| 方法 | 可行数 | 失败数 | 更好 | 持平 | 更差 | 恢复M2无解 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|---:|
| strong compact | 118 | 2 | 78 | 12 | 12 | 16 | -6.96% |
| Hybrid-Safety | 118 | 2 | 84 | 18 | 0 | 16 | -7.84% |

### 3.1 统一强初始解公平对比

上表中，Model 5 默认使用 strong initial，而 Model 2/3/4 使用 legacy initial。为拆分 strong initial 本身的贡献，额外测试了让 Model 2/3/4 也使用 strong initial 的公平版本。

输出：

```text
output/experiments/model2345_all_nonbeijing_120datasets_hybrid_safety_iter80_seed42_strong_baselines.md
output/experiments/model2345_all_nonbeijing_120datasets_hybrid_safety_iter80_seed42_strong_baselines.csv
```

总体结果：

| 模型 | 可行数 | 失败数 | 比强M2更好 | 持平 | 更差 | M2无解但可行 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|---:|
| Model 2 + strong initial | 118 | 2 | 0 | 118 | 0 | 0 | 0.00% |
| Model 3 + strong initial | 118 | 2 | 23 | 47 | 48 | 0 | 0.82% |
| Model 4 + strong initial | 118 | 2 | 13 | 105 | 0 | 0 | -0.01% |
| Model 5-Hybrid-Safety | 118 | 2 | 82 | 36 | 0 | 0 | -2.53% |

分规模看 Model 5：

| 规模 | M5可行 | 更好 | 持平 | 更差 | 恢复M2无解 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|
| 5_customers | 30/30 | 5 | 25 | 0 | 0 | -0.72% |
| 10_customers | 30/30 | 21 | 9 | 0 | 0 | -3.40% |
| 50_customers | 29/30 | 28 | 1 | 0 | 0 | -3.03% |
| 100_customers | 29/30 | 28 | 1 | 0 | 0 | -3.01% |

结论：strong initial 本身是一个非常强的可行性增强，会把 Model 2 从 `102/120` 可行提升到 `118/120` 可行，也会压缩 Model 5 的相对 gap；但统一强初始解后，Model 5 仍然在 82 个可比算例上优于强 Model 2，且 0 worse，说明 Model 5 的优势并不只来自初始解。

## 4. Model 5 更差问题与修复实验

早期 strong compact 120 全量实验中，Model 5 有 12 个 worse，主要集中在 5/10 客户小规模，以及 `50_customers_RC107`。当前 Hybrid-Safety 全量实测已经把 worse 降为 0。

### 4.1 尝试一：换随机种子

在 12 个 worse 算例上测试 strong initial，seed=7/99。

| 方法 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|
| strong seed42 当前结果 | 0 | 0 | 12 | 4.36% |
| strong seed7 | 1 | 0 | 11 | 3.98% |
| strong seed99 | 1 | 0 | 11 | 3.81% |

结论：多 seed 能修一部分，但不能作为防劣化主机制。

### 4.2 尝试二：legacy initial safety portfolio

在同样 12 个 worse 算例上，加入 legacy initial + guard 兜底分支。

输出：

```text
output/experiments/model5_large_portfolio_12datasets_legacy_initial_worse12_legacy_iter80_seed42_summary.md
output/experiments/model5_large_portfolio_12datasets_strong_initial_hybrid_safety_worse12_hybrid_refine_iter80_seed42_summary.md
```

结果：

| 方法 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|
| legacy safety only | 6 | 6 | 0 | -2.09% |
| strong + legacy hybrid safety | 6 | 6 | 0 | -2.09% |
| hybrid safety + route refine 分支 | 6 | 6 | 0 | -2.09% |

结论：`legacy_guard_route_refine_T1S1` 没有继续打开 6 个持平样本。当前关键不是再对中转后路线做小修，而是把 strong initial 与 legacy guard 同时纳入候选集合，防止强初始解在小规模上把搜索带偏。

12 个 worse 的修复明细：

| 算例 | Model 2 | 当前 strong | hybrid best | 结论 |
|---|---:|---:|---:|---|
| 10 C109 | 656.92 | 665.67 | 656.92 | 持平 |
| 10 C203 | 603.15 | 633.77 | 564.66 | 更好 |
| 10 R105 | 663.30 | 686.12 | 658.78 | 更好 |
| 10 R109 | 560.61 | 570.97 | 560.61 | 持平 |
| 10 R201 | 545.94 | 552.98 | 545.94 | 持平 |
| 10 R207 | 603.45 | 635.69 | 603.45 | 持平 |
| 10 RC105 | 674.81 | 694.45 | 644.10 | 更好 |
| 10 RC203 | 614.47 | 663.36 | 597.53 | 更好 |
| 10 RC207 | 566.05 | 611.69 | 565.52 | 更好 |
| 50 RC107 | 1738.55 | 1855.03 | 1554.43 | 更好 |
| 5 RC103 | 424.05 | 433.03 | 424.05 | 持平 |
| 5 RC108 | 527.73 | 560.98 | 527.73 | 持平 |

### 4.3 修复后全量实测

加入 legacy safety 分支后，重新运行 120 个非北京算例，得到实测结果：

| 方法 | 可行数 | 失败数 | 更好 | 持平 | 更差 | 恢复M2无解 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|---:|
| 当前 strong compact | 118 | 2 | 78 | 12 | 12 | 16 | -6.96% |
| M5-Hybrid-Safety 实测 | 118 | 2 | 84 | 18 | 0 | 16 | -7.84% |
| 外层 Model2 fallback 估计 | 118 | 2 | 78 | 24 | 0 | 16 | -7.47% |

结论：**最推荐 M5-Hybrid-Safety**。它不需要直接把 Model 2 当最终兜底，而是把 legacy initial + guard 作为模型五内部候选分支，既保留 strong initial 的大规模优势，又消除小规模劣化。

### 4.4 尝试三：风险感知、穷举稀疏中转与前缀 beam 补充分支

继续在 M5-Hybrid-Safety 基础上新增 4 个补充分支：

| 分支 | 核心作用 |
|---|---|
| `risk_sparse_guard_T1S1` | 稀疏中转 + baseline guard，并把时间余量、容量余量加入中转评分 |
| `risk_nearest_guard_T5S3` | nearest_k 候选 + 较宽中转上限，优先选择风险更低的中转 |
| `exhaustive_sparse_guard_T1S1` | T1/S1 稀疏约束下做更完整候选枚举 |
| `prefix_beam_T2S1` | beam + best_prefix，允许最多两步中转但只保留最好前缀 |

工程实现：

```text
models/model_3/alns_solver.py
  optimize_transshipment 增加 risk-aware scoring、slack/capacity guard

models/model_5/alns_solver.py
  透传 risk-aware 中转参数

experiments/run_model5_large_portfolio.py
  新增 OPTIMIZED_EXTRA_VARIANTS

experiments/run_model5_optimized_comparison.py
  同时比较 Model 2、Model 3、当前 M5、优化 M5
```

#### 小规模实验

实验设置：

```text
数据：5_customers + 10_customers，共 60 个非北京算例
iterations = 60
time = 6
transshipment_time_budget = 0.02
Model 2/3 使用 strong initial
```

输出：

```text
output/experiments/model5_optimized_comparison_60datasets_iter60_seed42_strong_baselines_small_5_10.md
output/experiments/model5_optimized_comparison_60datasets_iter60_seed42_strong_baselines_small_5_10.csv
```

总体结果：

| 对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Model 3 vs Model 2 | 5 | 44 | 11 | 0 | 0.67% |
| Current M5 vs Model 2 | 26 | 34 | 0 | 0 | -2.10% |
| Optimized M5 vs Model 2 | 27 | 33 | 0 | 0 | -2.18% |
| Optimized M5 vs Current M5 | 2 | 58 | 0 | 0 | -0.08% |

优化分支带来额外提升的算例：

| 算例 | Current M5 | Optimized M5 | 胜出分支 | Opt vs Current |
|---|---:|---:|---|---:|
| 5_customers_RC208 | 350.08 | 343.80 | `prefix_beam_T2S1` | -1.79% |
| 10_customers_C109 | 656.92 | 638.71 | `risk_nearest_guard_T5S3` | -2.77% |

#### 大规模代表样本

实验设置：

```text
数据：50/100_customers 代表样本 24 个，覆盖 C/R/RC 类型
iterations = 40
time = 5
transshipment_time_budget = 0.015
Model 2/3 使用 strong initial
```

输出：

```text
output/experiments/model5_optimized_comparison_12datasets_iter40_seed42_strong_baselines_large_sample_50_100.md
output/experiments/model5_optimized_comparison_12datasets_iter40_seed42_strong_baselines_large_sample_50_100.csv
output/experiments/model5_optimized_comparison_12datasets_iter40_seed42_strong_baselines_large_sample2_50_100.md
output/experiments/model5_optimized_comparison_12datasets_iter40_seed42_strong_baselines_large_sample2_50_100.csv
```

两组大规模样本合计结果：

| 对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Model 3 vs Model 2 | 11 | 1 | 10 | 2 | -0.25% |
| Current M5 vs Model 2 | 22 | 0 | 0 | 2 | -3.45% |
| Optimized M5 vs Model 2 | 22 | 0 | 0 | 2 | -3.88% |
| Optimized M5 vs Current M5 | 4 | 18 | 0 | 2 | -0.43% |

优化分支带来额外提升的算例：

| 算例 | Current M5 | Optimized M5 | 胜出分支 | Opt vs Current |
|---|---:|---:|---|---:|
| 50_customers_R201 | 1502.86 | 1488.04 | `exhaustive_sparse_guard_T1S1` | -0.99% |
| 50_customers_RC201 | 1645.87 | 1559.67 | `exhaustive_sparse_guard_T1S1` | -5.24% |
| 50_customers_R107 | 1884.19 | 1878.73 | `prefix_beam_T2S1` | -0.29% |
| 100_customers_RC108 | 2478.01 | 2402.48 | `risk_nearest_guard_T5S3` | -3.05% |

结论：新增优化分支不是全面替代当前 M5-Hybrid-Safety，但作为 portfolio 补充是有效的。它在小规模和大规模样本中都保持 `0 worse`，并能在少数 R/RC 或特殊 C 类算例上继续降低成本。

### 4.5 尝试四：停滞增强与风险精修 V2 分支

在 V1 优化分支基础上继续新增 4 个 V2 补充分支：

| 分支 | 核心作用 |
|---|---|
| `stagnation_exhaustive_T1S1` | 搜索停滞后，在 T1/S1 约束下临时加大枚举强度 |
| `stagnation_nearest_T5S3` | 搜索停滞后，对 nearest_k 候选扩大候选数和插入位置 |
| `stagnation_prefix_beam_T3S1` | 搜索停滞后，把 best-prefix beam 深度从 2 提到 3 |
| `risk_refine_guard_T5S3` | 风险感知 + conservative + prune/final refine/route refine |

工程实现：

```text
models/model_5/alns_solver.py
  新增 transshipment_stagnation_boost 系列参数；
  当若干轮没有产生更优解时，临时提高中转试探深度、时间预算和候选强度。

experiments/run_model5_large_portfolio.py
  新增 STAGNATION_EXTRA_VARIANTS；
  OPTIMIZED_EXTRA_VARIANTS = V1_OPTIMIZED_EXTRA_VARIANTS + STAGNATION_EXTRA_VARIANTS。

experiments/run_model5_optimized_comparison.py
  输出 Current M5、Optimized V1、Optimized V2 三层对比。
```

#### 小规模实验

实验设置：

```text
数据：5_customers + 10_customers，共 60 个非北京算例
iterations = 60
time = 6
transshipment_time_budget = 0.02
Model 2/3 使用 strong initial
```

输出：

```text
output/experiments/model5_optimized_comparison_60datasets_iter60_seed42_strong_baselines_small_5_10_v2.md
output/experiments/model5_optimized_comparison_60datasets_iter60_seed42_strong_baselines_small_5_10_v2.csv
```

总体结果：

| 对比 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|
| Current M5 vs Model 2 | 26 | 34 | 0 | -2.10% |
| Optimized V1 vs Model 2 | 27 | 33 | 0 | -2.18% |
| Optimized V2 vs Model 2 | 31 | 29 | 0 | -2.35% |
| Optimized V2 vs V1 | 6 | 54 | 0 | -0.17% |
| Optimized V2 vs Current M5 | 8 | 52 | 0 | -0.25% |

V2 相比 V1 新增改善：

| 算例 | V1 | V2 | 胜出分支 | V2 vs V1 |
|---|---:|---:|---|---:|
| 5_customers_C103 | 358.52 | 339.82 | `risk_refine_guard_T5S3` | -5.22% |
| 10_customers_C201 | 584.16 | 582.34 | `stagnation_exhaustive_T1S1` | -0.31% |
| 10_customers_R105 | 674.16 | 646.38 | `risk_refine_guard_T5S3` | -4.12% |
| 10_customers_RC103 | 624.04 | 623.72 | `risk_refine_guard_T5S3` | -0.05% |
| 10_customers_RC107 | 619.73 | 619.37 | `risk_refine_guard_T5S3` | -0.06% |
| 10_customers_RC205 | 630.21 | 627.29 | `risk_refine_guard_T5S3` | -0.46% |

#### 大规模代表样本

实验设置：

```text
数据：50/100_customers 代表样本 24 个，覆盖 C/R/RC 类型
iterations = 40
time = 5
transshipment_time_budget = 0.015
Model 2/3 使用 strong initial
```

输出：

```text
output/experiments/model5_optimized_comparison_12datasets_iter40_seed42_strong_baselines_large_sample_50_100_v2.md
output/experiments/model5_optimized_comparison_12datasets_iter40_seed42_strong_baselines_large_sample_50_100_v2.csv
output/experiments/model5_optimized_comparison_12datasets_iter40_seed42_strong_baselines_large_sample2_50_100_v2.md
output/experiments/model5_optimized_comparison_12datasets_iter40_seed42_strong_baselines_large_sample2_50_100_v2.csv
```

两组大规模样本合计结果：

| 对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Current M5 vs Model 2 | 22 | 0 | 0 | 2 | -3.50% |
| Optimized V1 vs Model 2 | 22 | 0 | 0 | 2 | -3.88% |
| Optimized V2 vs Model 2 | 22 | 0 | 0 | 2 | -3.90% |
| Optimized V2 vs V1 | 3 | 19 | 0 | 2 | -0.02% |
| Optimized V2 vs Current M5 | 5 | 17 | 0 | 2 | -0.40% |

V2 相比 V1 新增改善：

| 算例 | V1 | V2 | 胜出分支 | V2 vs V1 |
|---|---:|---:|---|---:|
| 50_customers_C209 | 1438.76 | 1438.64 | `risk_refine_guard_T5S3` | -0.01% |
| 50_customers_R107 | 1878.73 | 1873.97 | `stagnation_prefix_beam_T3S1` | -0.25% |
| 100_customers_RC108 | 2402.48 | 2400.63 | `stagnation_nearest_T5S3` | -0.08% |

结论：V2 分支值得保留，但定位仍是补充分支。小规模收益更明显，尤其 `risk_refine_guard_T5S3`；大规模收益较小，主要来自停滞后的 beam/nearest 增强。当前不建议继续无上限增加 portfolio 宽度，下一步更合理的是把 `risk_refine_guard_T5S3` 和两个停滞分支作为优先候选，减少弱分支的运行开销。

### 4.6 尝试五：V3 跨车重排与 V4 任务对交换

继续观察 V1/V2 结果后，一个明显问题是：很多中转试探已经改变了 pickup 的物理位置，但最终 E2 路线之间的顾客任务分布仍可能不均衡。于是新增两个最终精修方向：

| 版本 | 分支 | 核心动作 | 定位 |
|---|---|---|---|
| V3 | `rebalance_guard_T1S1`、`risk_rebalance_guard_T5S3`、`stagnation_rebalance_T5S3` | 同一卫星内，把一个完整顾客任务对 `delivery + pickup` 从一条 E2 路线搬到另一条 E2 路线 | 主推荐新增方向 |
| V4 | `swap_rebalance_guard_T1S1`、`risk_swap_rebalance_T5S3` | 同一卫星内，两条 E2 路线交换两个完整顾客任务对 | 可选补充分支 |

工程约束：

```text
1. 只在同一卫星的 E2 路线之间操作；
2. 每次移动/交换的是完整顾客任务对，避免拆散 delivery 与 pickup；
3. 不直接移动虚拟中转节点，只移动真实顾客任务；
4. 每次试探后重新 synchronize_transshipments；
5. 必须通过 evaluator.is_feasible；
6. 只有 evaluator.total_cost 真实下降才接受。
```

新增工程参数：

```text
inter_route_rebalance
inter_route_rebalance_rounds
inter_route_rebalance_max_moves
inter_route_rebalance_max_pairs
inter_route_rebalance_position_top_k
inter_route_pair_swap
inter_route_pair_swap_max_pairs
```

#### 4.6.1 V3 跨车重排结果

实验设置：

```text
数据：5/10_customers 共 60 个 + 50/100_customers 代表样本 24 个
iterations = 60, time = 6  用于 5/10
iterations = 40, time = 5  用于 50/100
Model 2/3 使用 strong initial
不使用北京算例
```

84 个算例合计结果：

| 对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Model 3 vs Model 2 | 15 | 45 | 22 | 2 | 0.43% |
| Current M5 vs Model 2 | 48 | 34 | 0 | 2 | -2.52% |
| Optimized V1 vs Model 2 | 49 | 33 | 0 | 2 | -2.67% |
| Optimized V2 vs Model 2 | 53 | 29 | 0 | 2 | -2.80% |
| Optimized V3 vs Model 2 | 56 | 26 | 0 | 2 | -3.31% |
| V3 vs V2 | 19 | 63 | 0 | 2 | -0.53% |
| V3 vs Current M5 | 27 | 55 | 0 | 2 | -0.80% |

V3 相比 V2 的主要新增改善：

| 算例 | V2 | V3 | 胜出分支 | 节省 |
|---|---:|---:|---|---:|
| 10_customers_R107 | 689.36 | 646.26 | `rebalance_guard_T1S1` | -43.10 |
| 10_customers_R109 | 569.79 | 532.84 | `rebalance_guard_T1S1` | -36.95 |
| 50_customers_C209 | 1438.64 | 1401.70 | `risk_rebalance_guard_T5S3` | -36.94 |
| 10_customers_C109 | 638.71 | 602.57 | `rebalance_guard_T1S1` | -36.14 |
| 100_customers_C209 | 2114.79 | 2078.82 | `risk_rebalance_guard_T5S3` | -35.97 |
| 50_customers_RC201 | 1559.67 | 1526.09 | `stagnation_rebalance_T5S3` | -33.58 |
| 10_customers_C103 | 514.40 | 487.13 | `rebalance_guard_T1S1` | -27.27 |
| 100_customers_R107 | 2516.80 | 2490.39 | `risk_rebalance_guard_T5S3` | -26.41 |

结论：V3 是这几轮继续优化里最有效的一轮。它说明“中转之后做路线间任务再均衡”确实值得做，尤其对 10 客户和 50/100 的 C/R/RC 混合结构都能产生真实收益。

#### 4.6.2 V4 任务对交换结果

V4 在 V3 基础上继续加入两条交换分支。由于 V4 是 portfolio 补充，最终结果不会比 V3 差；重点看它是否能新增超过 V3 的最终赢点。

84 个算例合计结果：

| 对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Current M5 vs Model 2 | 48 | 34 | 0 | 2 | -2.50% |
| Optimized V1 vs Model 2 | 49 | 33 | 0 | 2 | -2.66% |
| Optimized V2 vs Model 2 | 53 | 29 | 0 | 2 | -2.81% |
| Optimized V3 vs Model 2 | 56 | 26 | 0 | 2 | -3.32% |
| Optimized V4 vs Model 2 | 56 | 26 | 0 | 2 | -3.33% |
| V3 vs V2 | 20 | 62 | 0 | 2 | -0.52% |
| V4 vs V3 | 3 | 79 | 0 | 2 | -0.01% |
| V4 vs Current M5 | 30 | 52 | 0 | 2 | -0.84% |

分规模看 V4 相比 V3：

| 规模 | 算例数 | V4 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|
| 5_customers | 30 | 0 | 30 | 0 | 0 | 0.00% |
| 10_customers | 30 | 0 | 30 | 0 | 0 | 0.00% |
| 50_customers | 12 | 2 | 9 | 0 | 1 | -0.04% |
| 100_customers | 12 | 1 | 10 | 0 | 1 | -0.02% |
| 50/100 合计 | 24 | 3 | 19 | 0 | 2 | -0.03% |

V4 相比 V3 新增的最终改善：

| 算例 | V3 | V4 | 胜出分支 | 节省 |
|---|---:|---:|---|---:|
| 100_customers_C209 | 2103.42 | 2097.85 | `swap_rebalance_guard_T1S1` | -5.57 |
| 50_customers_RC108 | 1549.65 | 1544.50 | `swap_rebalance_guard_T1S1` | -5.15 |
| 50_customers_C201 | 1459.92 | 1457.63 | `swap_rebalance_guard_T1S1` | -2.29 |

交换分支本身的局部贡献更明显：

| 分支对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| `swap_rebalance_guard_T1S1` vs `rebalance_guard_T1S1` | 14 | 67 | 1 | 2 | -0.13% |
| `risk_swap_rebalance_T5S3` vs `risk_rebalance_guard_T5S3` | 15 | 64 | 3 | 2 | -0.18% |

结论：V4 有局部价值，但最终新增收益远小于 V3。它可以保留为大规模实验的可选补充分支；若要控制运行时间，推荐优先保留 V3，V4 不作为默认必跑分支。

V3/V4 输出：

```text
output/experiments/model5_optimized_comparison_60datasets_iter60_seed42_strong_baselines_small_5_10_v3.md
output/experiments/model5_optimized_comparison_12datasets_iter40_seed42_strong_baselines_large_sample_50_100_v3.md
output/experiments/model5_optimized_comparison_12datasets_iter40_seed42_strong_baselines_large_sample2_50_100_v3.md
output/experiments/model5_optimized_comparison_60datasets_iter60_seed42_strong_baselines_small_5_10_v4.md
output/experiments/model5_optimized_comparison_12datasets_iter40_seed42_strong_baselines_large_sample_50_100_v4.md
output/experiments/model5_optimized_comparison_12datasets_iter40_seed42_strong_baselines_large_sample2_50_100_v4.md
```

### 4.7 尝试六：V5 迭代跨车重排/交换

在 V3/V4 里，跨车重排和任务对交换发生在 ALNS 搜索结束后的最终精修阶段。进一步的 V5 尝试是：把小范围跨车重排也放进 ALNS candidate 的迭代过程中。

核心流程：

```text
每轮 ALNS 生成 candidate
-> candidate 内做受控中转试探
-> 清理低贡献中转
-> 满足触发条件时做小范围跨车重排/交换
-> 重新计算 candidate 总成本
-> 交给 ALNS 接受准则
```

关键点是：迭代重排仍然属于本轮 candidate。若该 candidate 未被 ALNS 接受，重排结果不会进入下一轮；若 ALNS 接受，则在重排基础上继续搜索。

本轮测试了 3 个 V5 分支：

| 分支 | 触发方式 | 每次动作上限 | 核心含义 |
|---|---|---:|---|
| `v5_fixed_rebalance_T1S1` | 每 10 轮固定触发 | 1 次搬移 | 主动、轻量地做迭代跨车重排 |
| `v5_stag_rebalance_T1S1` | 停滞 12 轮后触发，冷却 8 轮 | 2 次搬移 | 搜索卡住时扩大一点重排强度 |
| `v5_stag_swap_T1S1` | 停滞触发 | 2 次搬移 + 交换 | 在停滞时同时尝试任务对交换 |

工程约束：

```text
1. 只在同一卫星的 E2 路线之间重排；
2. 每次搬移的是完整顾客任务对 delivery + pickup；
3. 不直接移动虚拟中转节点；
4. 每次试探后重新 synchronize_transshipments；
5. 必须通过 evaluator.is_feasible；
6. 只有 evaluator.total_cost 真实下降才接受本次重排/交换。
```

新增工程参数：

```text
iterative_rebalance
iterative_rebalance_trigger = fixed / stagnation
iterative_rebalance_interval
iterative_rebalance_stagnation_window
iterative_rebalance_rounds
iterative_rebalance_max_moves
iterative_rebalance_max_pairs
iterative_rebalance_position_top_k
iterative_rebalance_pair_swap
iterative_rebalance_pair_swap_max_pairs
```

#### 4.7.1 小规模实验

实验设置：

```text
数据：5_customers + 10_customers，共 60 个非北京算例
iterations = 60
time = 6
transshipment_time_budget = 0.02
Model 2/3 使用 strong initial
```

输出：

```text
output/experiments/model5_optimized_comparison_60datasets_iter60_seed42_strong_baselines_small_5_10_v5.md
output/experiments/model5_optimized_comparison_60datasets_iter60_seed42_strong_baselines_small_5_10_v5.csv
```

总体结果：

| 对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Current M5 vs Model 2 | 26 | 34 | 0 | 0 | -2.10% |
| Optimized V1 vs Model 2 | 27 | 33 | 0 | 0 | -2.18% |
| Optimized V2 vs Model 2 | 31 | 29 | 0 | 0 | -2.35% |
| Optimized V3 vs Model 2 | 34 | 26 | 0 | 0 | -2.89% |
| Optimized V4 vs Model 2 | 34 | 26 | 0 | 0 | -2.89% |
| Optimized V5 vs Model 2 | 34 | 26 | 0 | 0 | -3.01% |
| V5 vs V4 | 4 | 56 | 0 | 0 | -0.13% |

V5 相比 V4 的新增最终改善：

| 算例 | V4 | V5 | 胜出分支 | 节省 |
|---|---:|---:|---|---:|
| 5_customers_R203 | 447.85 | 438.07 | `v5_stag_rebalance_T1S1` | -9.78 |
| 10_customers_C109 | 602.57 | 595.67 | `v5_fixed_rebalance_T1S1` | -6.90 |
| 10_customers_C201 | 582.34 | 573.96 | `v5_fixed_rebalance_T1S1` | -8.38 |
| 10_customers_C203 | 577.09 | 559.22 | `v5_fixed_rebalance_T1S1` | -17.87 |

#### 4.7.2 大规模代表样本

实验设置：

```text
数据：50/100_customers 代表样本 24 个，覆盖 C/R/RC 类型
iterations = 40
time = 5
transshipment_time_budget = 0.015
Model 2/3 使用 strong initial
```

输出：

```text
output/experiments/model5_optimized_comparison_12datasets_iter40_seed42_strong_baselines_large_sample_50_100_v5.md
output/experiments/model5_optimized_comparison_12datasets_iter40_seed42_strong_baselines_large_sample_50_100_v5.csv
output/experiments/model5_optimized_comparison_12datasets_iter40_seed42_strong_baselines_large_sample2_50_100_v5.md
output/experiments/model5_optimized_comparison_12datasets_iter40_seed42_strong_baselines_large_sample2_50_100_v5.csv
```

两组大规模样本合计结果：

| 对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Current M5 vs Model 2 | 22 | 0 | 0 | 2 | -3.45% |
| Optimized V1 vs Model 2 | 22 | 0 | 0 | 2 | -3.91% |
| Optimized V2 vs Model 2 | 22 | 0 | 0 | 2 | -3.92% |
| Optimized V3 vs Model 2 | 22 | 0 | 0 | 2 | -4.28% |
| Optimized V4 vs Model 2 | 22 | 0 | 0 | 2 | -4.52% |
| Optimized V5 vs Model 2 | 22 | 0 | 0 | 2 | -4.69% |
| V5 vs V4 | 4 | 18 | 0 | 2 | -0.18% |

V5 相比 V4 的新增最终改善：

| 算例 | V4 | V5 | 胜出分支 | 节省 |
|---|---:|---:|---|---:|
| 100_customers_R201 | 2433.76 | 2417.15 | `v5_fixed_rebalance_T1S1` | -16.61 |
| 100_customers_RC101 | 3323.84 | 3296.65 | `v5_stag_swap_T1S1` | -27.19 |
| 100_customers_C109 | 1987.94 | 1952.96 | `v5_fixed_rebalance_T1S1` | -34.98 |
| 100_customers_C209 | 2097.85 | 2082.86 | `v5_stag_swap_T1S1` | -14.99 |

#### 4.7.3 84 个样本合计

| 对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Model 3 vs Model 2 | 15 | 45 | 22 | 2 | 0.43% |
| Current M5 vs Model 2 | 48 | 34 | 0 | 2 | -2.46% |
| Optimized V1 vs Model 2 | 49 | 33 | 0 | 2 | -2.64% |
| Optimized V2 vs Model 2 | 53 | 29 | 0 | 2 | -2.77% |
| Optimized V3 vs Model 2 | 56 | 26 | 0 | 2 | -3.26% |
| Optimized V4 vs Model 2 | 56 | 26 | 0 | 2 | -3.33% |
| Optimized V5 vs Model 2 | 56 | 26 | 0 | 2 | -3.46% |
| V5 vs V4 | 8 | 74 | 0 | 2 | -0.14% |
| V5 vs Current M5 | 36 | 46 | 0 | 2 | -1.02% |

分规模看 V5：

| 规模 | 算例数 | V5 vs Model 2：更好 | 持平 | 更差 | 均失败 | 平均 gap% | V5 vs V4：更好 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 5_customers | 30 | 6 | 24 | 0 | 0 | -0.99% | 1 | -0.07% |
| 10_customers | 30 | 28 | 2 | 0 | 0 | -5.03% | 3 | -0.19% |
| 50_customers | 12 | 11 | 0 | 0 | 1 | -5.14% | 0 | 0.00% |
| 100_customers | 12 | 11 | 0 | 0 | 1 | -4.24% | 4 | -0.36% |

V5 分支观察：

| 观察项 | 结果 |
|---|---|
| 最终 portfolio 由 V5 分支直接胜出的次数 | 8 个算例 |
| V5-only 三个分支内部最常最优 | `v5_stag_swap_T1S1` 和 `v5_fixed_rebalance_T1S1` |
| 相对 V4 的主要贡献者 | `v5_fixed_rebalance_T1S1` 贡献 5 个新增赢点，`v5_stag_swap_T1S1` 贡献约 2.5 个新增赢点 |
| 适用性 | 固定间隔重排更适合主动打开 C/R 类结构；停滞交换更适合 100 客户 C/RC 局部卡住的情况 |

结论：V5 迭代跨车重排不是像 V3 那样的大幅跃升，但它在 V4 已经很强的基础上继续做到 `8 更好 / 74 持平 / 0 更差 / 平均 -0.14%`。这说明“中转后路线再均衡”不仅适合作为最终精修，也可以作为 ALNS 过程中的轻量 candidate 改进。V5-A 固定间隔重排和 V5-C 停滞交换应继续保留；V5-B 单独停滞重排收益较少，可作为备选。

### 4.8 尝试七：V6 稳定增强组合

V6 没有替换 V5 的安全框架，而是在 V5 portfolio 后追加 4 个稳定增强分支：

```text
1. v6_fixed_rebalance_T2S1
2. v6_stag_swap_T2S1
3. v6_cand_refine_rebalance_T1S1
4. v6_risk_cand_rebalance_T5S3
```

核心变化：

```text
1. 把部分 V5 的 T1/S1 扩到 T2/S1，允许更充分的跨车再均衡；
2. 在候选解阶段加入小范围 route-local refine，减少中转后局部路线未整理的问题；
3. 对风险感知 nearest-k 分支加入 candidate refine + final rebalance；
4. 保持 safety portfolio 取最优，所以在同一批运行结果中不会劣于 V5。
```

84 个样本合计结果：

| 对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Model 3 vs Model 2 | 15 | 45 | 22 | 2 | 0.43% |
| Current M5 vs Model 2 | 48 | 34 | 0 | 2 | -2.48% |
| Optimized V5 vs Model 2 | 56 | 26 | 0 | 2 | -3.44% |
| Stable V6 vs Model 2 | 56 | 26 | 0 | 2 | -3.49% |
| Stable V6 vs Current M5 | 37 | 45 | 0 | 2 | -1.04% |
| Stable V6 vs Optimized V5 | 8 | 74 | 0 | 2 | -0.06% |

分规模看，V6 的新增收益集中在 50/100 客户大规模算例：

| 规模 | V6 vs Model 2：更好 | 持平 | 更差 | 均失败 | 平均 gap% | V6 vs V5：更好 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|---:|
| 5_customers | 6 | 24 | 0 | 0 | -0.99% | 0 | 0.00% |
| 10_customers | 28 | 2 | 0 | 0 | -5.05% | 0 | 0.00% |
| 50_customers | 11 | 0 | 0 | 1 | -5.42% | 3 | -0.15% |
| 100_customers | 11 | 0 | 0 | 1 | -4.11% | 5 | -0.27% |

V6 相比 V5 的 8 个新增赢点：

| 算例 | V5 | V6 | 胜出分支 | 节省 |
|---|---:|---:|---|---:|
| 50_customers_C101 | 1345.48 | 1339.28 | `v6_stag_swap_T2S1` | -6.20 |
| 100_customers_C101 | 2001.45 | 1991.68 | `v6_risk_cand_rebalance_T5S3` | -9.77 |
| 100_customers_RC101 | 3296.65 | 3252.84 | `v6_fixed_rebalance_T2S1` | -43.81 |
| 50_customers_C209 | 1406.97 | 1404.84 | `v6_risk_cand_rebalance_T5S3` | -2.13 |
| 50_customers_R207 | 1492.87 | 1477.09 | `v6_risk_cand_rebalance_T5S3` | -15.78 |
| 100_customers_C209 | 2084.29 | 2082.69 | `v6_cand_refine_rebalance_T1S1` | -1.60 |
| 100_customers_R207 | 2471.77 | 2457.99 | `v6_stag_swap_T2S1` | -13.78 |
| 100_customers_RC108 | 2411.43 | 2397.90 | `v6_risk_cand_rebalance_T5S3` | -13.53 |

结论：V6 是目前更适合替换原 V5 的默认优化版本。它不改变 V5 的安全边界，新增收益虽不大，但在 50/100 客户样本中稳定出现，且 84 个样本未观察到回退。当前标准优化脚本已把 V6 分支并入 `OPTIMIZED_EXTRA_VARIANTS`，并统一采用 Full V6：所有规模都运行 4 个 V6 extra 分支，避免实验口径分裂。

## 5. 当前推荐版本

推荐命名：

```text
Model 5-Hybrid-Safety-Optimized-V6
```

包含：

```text
1. 每轮 ALNS candidate 受控中转试探；
2. H=1 为主，避免连续中转过度累积；
3. T1/S1 稀疏中转为主，T5/S3 作为少数补充；
4. strong initial 负责恢复大规模可行性；
5. legacy initial + baseline guard 负责防止小规模劣化；
6. risk-aware / exhaustive sparse / prefix beam 作为补充分支；
7. V3 跨车重排作为推荐最终精修；
8. V4 任务对交换作为大规模可选补充分支；
9. V6 稳定增强组合，包含 V5 迭代重排、T2/S1 扩展、candidate refine 和风险感知重排；
10. portfolio 取最优结果；
11. 跨车重排距离收益剪枝，用于降低无效完整可行性检查开销。
```

工程实现：

```text
experiments/run_model5_large_portfolio.py
  BASE_COMPACT_VARIANTS + LEGACY_SAFETY_VARIANTS + OPTIMIZED_EXTRA_VARIANTS

experiments/run_model2345_all_nonbeijing.py
  自动使用 hybrid safety portfolio

experiments/run_model5_optimized_comparison.py
  用于比较 current M5、Optimized V1/V2/V3/V4/V5/Stable V6
  默认运行 Full Stable V6 extra

models/model_5/alns_solver.py
  _final_inter_route_rebalance 在完整可行性检查前先做距离收益剪枝
```

最终判断：

```text
Model 5 的价值已经比较明确：
1. 相比 Model 2，能显著改善大规模 50/100 客户结果；
2. 能恢复 Model 2 无法得到可行解的 R/RC 算例；
3. 通过 legacy safety 分支，可以把 worse 降到 0；
4. 风险感知、稀疏穷举和前缀 beam 可以作为补充分支继续压低部分算例成本；
5. V3 跨车重排证明中转后的路线再均衡值得做，是当前新增优化中最强的一项；
6. V4 任务对交换只带来小幅额外收益，建议作为可选分支而非默认替代；
7. V6 在 84 个样本中相对 V5 做到 `8 更好 / 74 持平 / 0 更差 / 平均 -0.06%`，新增收益集中在 50/100 客户；
8. 后续优化重点不应是盲目增加中转次数，而是继续改进中转候选质量和跨车重排效率。
```
