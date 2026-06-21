# 2E-VRPTW-PDD

两级带时间窗取送货车辆路径问题求解框架，包含 Model 1-5、Gurobi 精确求解器和 ALNS 启发式求解器。

## 当前重点

当前主线是 **Model 5-Hybrid-Safety-Optimized-V6**：

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
docs/model5_optimization_summary.md
```

## 最新完整公平对比（Full V6）

数据：`data/5_customers`、`10_customers`、`50_customers`、`100_customers`，共 120 个算例，不含 `data/beijing_fresh`。

实验设置：

```text
iterations = 60
time = 6 per solver/variant
seed = 42
transshipment_time_budget = 0.02
Model 2/3/4 strong initial = True
Model 5 portfolio = optimized_v6 (Full V6)
```

说明：这是当前推荐口径的完整公平对比。Model 2/3/4 同样启用 strong initial，Model 5 使用 Full V6 portfolio 取最优；计算量高于单次 ALNS，结果主要用于比较解质量。

当前实测结果：

| 模型 | 可行数 | 失败数 | 比强 Model 2 更好 | 持平 | 更差 | M2无解但可行 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|---:|
| Model 2 + strong initial | 118 | 2 | 0 | 118 | 0 | 0 | 0.00% |
| Model 3 + strong initial | 118 | 2 | 35 | 49 | 34 | 0 | 0.42% |
| Model 4 + strong initial | 118 | 2 | 18 | 100 | 0 | 0 | -0.03% |
| Model 5-Hybrid-Safety-Optimized-V6 | 118 | 2 | 92 | 26 | 0 | 0 | -3.91% |

分规模结果：

| 规模 | M5 更好 | M5 持平 | M5 更差 | 均失败 | M5 平均 gap% |
|---|---:|---:|---:|---:|---:|
| 5_customers | 6 | 24 | 0 | 0 | -0.99% |
| 10_customers | 28 | 2 | 0 | 0 | -5.05% |
| 50_customers | 29 | 0 | 0 | 1 | -4.54% |
| 100_customers | 29 | 0 | 0 | 1 | -5.11% |

完整输出：

```text
output/experiments/model2345_all_nonbeijing_120datasets_optimized_v6_iter60_seed42_strong_baselines.md
output/experiments/model2345_all_nonbeijing_120datasets_optimized_v6_iter60_seed42_strong_baselines.csv
```

结论：在统一强初始解后，当前 Full V6 M5 相比强 Model 2 做到 `92 更好 / 26 持平 / 0 更差`，平均成本下降 `3.91%`。两个均失败算例是 `100_customers_R101` 和 `50_customers_RC101`。

## 历史对照：Hybrid-Safety

为了拆分“strong initial 贡献”和“Model 5 中转机制贡献”，也测试了让 Model 2/3/4 同样使用 strong initial 的版本：

| 模型 | 可行数 | 失败数 | 比强 M2 更好 | 持平 | 更差 | M2无解但可行 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|---:|
| Model 2 + strong initial | 118 | 2 | 0 | 118 | 0 | 0 | 0.00% |
| Model 3 + strong initial | 118 | 2 | 23 | 47 | 48 | 0 | 0.82% |
| Model 4 + strong initial | 118 | 2 | 13 | 105 | 0 | 0 | -0.01% |
| Model 5-Hybrid-Safety | 118 | 2 | 82 | 36 | 0 | 0 | -2.53% |

结论：strong initial 本身把 Model 2 从 `102/120` 可行提升到 `118/120` 可行，因此原始对比里的部分优势确实来自初始解；但在统一强初始解后，Model 5 仍然做到 `82 更好 / 36 持平 / 0 更差`，说明中转迭代与 safety portfolio 仍有稳定增量收益。

```text
output/experiments/model2345_all_nonbeijing_120datasets_hybrid_safety_iter80_seed42_strong_baselines.md
output/experiments/model2345_all_nonbeijing_120datasets_hybrid_safety_iter80_seed42_strong_baselines.csv
```

## Model 5 继续优化分支

在当前 M5-Hybrid-Safety 上先后加入四层补充分支：

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
| Model 3 vs Model 2 | 15 | 45 | 22 | 2 | 0.43% |
| Current M5 vs Model 2 | 48 | 34 | 0 | 2 | -2.46% |
| Optimized V1 vs Model 2 | 49 | 33 | 0 | 2 | -2.64% |
| Optimized V2 vs Model 2 | 53 | 29 | 0 | 2 | -2.77% |
| Optimized V3 vs Model 2 | 56 | 26 | 0 | 2 | -3.26% |
| Optimized V4 vs Model 2 | 56 | 26 | 0 | 2 | -3.33% |
| Optimized V5 vs Model 2 | 56 | 26 | 0 | 2 | -3.44% |
| Stable V6 vs Model 2 | 56 | 26 | 0 | 2 | -3.49% |
| V3 vs V2 | 19 | 63 | 0 | 2 | -0.53% |
| V4 vs V3 | 3 | 79 | 0 | 2 | -0.06% |
| V5 vs V4 | 8 | 74 | 0 | 2 | -0.14% |
| V6 vs V5 | 8 | 74 | 0 | 2 | -0.06% |

结论：V3 跨车重排是最强的单次跃升；V4 交换有局部价值；V5 迭代重排/交换稳定释放中转后的路线均衡收益；V6 在 50/100 客户代表样本上继续增加 8 个赢点，在 5/10 客户上保持持平。当前标准优化版为 Full V6 portfolio，并在跨车重排中加入距离收益剪枝以减少无效完整可行性检查。

输出：

```text
output/experiments/model5_v6_stable_comparison_60datasets_iter60_seed42_strong_baselines_small_5_10_v6.md
output/experiments/model5_v6_stable_comparison_24datasets_iter40_seed42_strong_baselines_large_sample_50_100_v6.md
output/experiments/model5_v6_stable_comparison_84datasets_seed42_combined.md
```

## 常用命令

运行主程序：

```bash
python main.py
```

全部非北京算例对比：

```bash
python experiments/run_model2345_all_nonbeijing.py --iterations 80 --time 10 --transshipment-time-budget 0.03
```

Model 5 portfolio 单独实验：

```bash
python experiments/run_model5_large_portfolio.py \
  --datasets data/50_customers/C101.csv data/50_customers/C103.csv \
  --iterations 80 --time 10 --transshipment-time-budget 0.03
```

Model 5 当前版与优化补充分支对比：

```bash
python experiments/run_model5_optimized_comparison.py \
  --datasets data/5_customers data/10_customers \
  --iterations 60 --time 6 --transshipment-time-budget 0.02 \
  --strong-initial-baselines --tag small_5_10_v6
```

## 目录

```text
core/          实例、节点、车队数据结构
models/        Model 1-5 和 ALNS 公共组件
experiments/   批量实验与对比脚本
data/          Solomon 风格非北京算例和北京生鲜算例
docs/          当前实验总结文档
memory/        研究过程记录
output/        实验输出
```

旧版更详细说明保留在 `readme.txt`。
