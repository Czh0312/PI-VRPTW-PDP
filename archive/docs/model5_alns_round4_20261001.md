# M5 多批次 ALNS 第四轮：有向弧查询复用

**距离缓存仅保留为可选实验功能，默认关闭。** 距离枚举微基准耗时平均下降 **21.80%**，
但完整 60 对限时运行的距离基本持平（平均增加 **0.055%**），平均 CV 从 **1.78% 增至 2.25%**。
本轮没有实现更稳定的解质量，因此不升级默认策略。两版 **120 个方案全部通过独立审计和两周期重放**。
第三轮改变搜索策略的版本也已撤回，见 [失败记录](model5_alns_round3_20261001.md)。

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run + validate
- Verification Status: ANALYZED / EXPERIMENTAL_ONLY。冻结输入与源码、120 个方案重审计和统计复算通过；默认关闭新增缓存。
- 范围：固定分仓、B2/B3、有限池、截止回收、清洗与周期复用的 M5。

## 修改及未采用试验

第三轮搜索策略在 12 个开发作业上改善，但完整 78 对距离平均增加 1.05%，平均 CV 增大。
已撤回该策略，仅保留冻结证据，见 [第三轮失败记录](model5_alns_round3_20261001.md)。
第四轮的基线因此是恢复后的第二轮搜索规则，不能把各轮百分比相加。

实验模式的 `CycleDescent` 在同一次求解中准备订单到顾客的对应关系，
并用最多 4096 项的 LRU 复用有向弧距离。路线距离仍按原节点顺序相加，
移动/反转/整笔请求迁移与交换保留原候选顺序、浮点算术和距离门槛。
空路线仍为零，不用三角不等式替换有向、非度量距离。
没有缓存路线的时序或可行性；每个提案仍经局部时序筛查、完整解码及独立审计。

缓存属于一次 `CycleDescent` 上下文，新求解重新读取输入；不能在同一搜索中修改路网后复用旧缓存。
下降器的 `cache_distances` 默认是 `False`；公开 M5 Python 入口可显式传
`cache_descent_distances=True`。诊断增加 `descent_arc_cache_enabled` 和 `descent_arc_cache_hits/misses`。
公开 CLI 继续使用默认关闭的路径。数学约束、搜索算子、接受规则和预算策略不变。
单批次使用自身下降器，本轮没有修改其实现。

## 注册对照与验证

- 60 对注册作业，种子 42/43/44，每版求解预算 420 秒；独立进程串行运行。
- 50/100 顾客、B2/B3、全池 N=Q、紧池 N=max(最大单批需求, ceil(0.5Q))。
- 全部配置运行 5 秒；100 顾客 B3 另运行 15 秒，最多 100000 次迭代。
- 开发集：`100-5-2_subset50` 和 `100-5-3` 的全部 30 个作业。
  覆盖全部批次、池和预算，避免第三轮只选部分较弱组合的局限。
- 保留集：此前未测试的 `100-10-1_scenario02` 的嵌套 50/100 顾客。
  这是已见母例的新场景，不是全新母例；候选冻结前不查看保留集的距离质量。
- 只有一个统一候选，不按作业选取不同算法的最好结果。失败结果保留。
- 少量母例、嵌套规模和三个种子只支持描述性工程比较，不报告显著性或全局最优性。

行为验证使用八个开发配置（50/100、B2/B3、全/紧池）：固定时钟和 1000 次迭代，
逐次比较冻结两版的解码状态、候选顺序、最好解轨迹、时间表、审计及算子计数。
固定时钟隔离了原有墙钟降温和中断位置差异；实际同时间运行仍可能走不同轨迹。
枚举微基准每配置三次交替顺序、重新创建缓存，计入上下文准备时间。
该微基准只计量距离枚举，不是完整求解速度或质量证明。

新增回归覆盖有向非度量候选与时序筛查一致、4096 项界限、新上下文读取编辑后输入、
以及受控时钟的搜索轨迹一致；现有完整审计继续验证容器池与周期规则。

## 完整结果与保留决策

- 29 次距离改善、2 次持平、29 次退步；完整配对平均 +0.055%。
- 开发集平均 -0.56%，保留集平均 +0.67%。这些变化只描述本轮固定作业。
- 9/20 组 CV 下降，10/20 组最差种子距离改善；不能把枚举提速称为稳定性提升。
- 同预算实际迭代数的配对变化中位数 +7.54%，均值 +9.59%；弧查询命中率 99.73%。
  最大全周期求解超预算约 0.029 秒，不含子进程启动与输入读取。
- 相同场景/池/种子由 5 秒延长到 15 秒，两版均有 1/12 对距离变差。
  原墙钟降温仍会改变搜索轨迹，预算延长的单调性问题尚未解决。

全部 100 顾客、B3、15 秒分组如下；正值为距离退步：

| 场景 | 池 N | 距离均值：前 → 缓存开启 | 配对变化 | 最差距离：前 → 后 | CV：前 → 后 |
|---|---:|---:|---:|---:|---:|
| 100-5-3 | 156 | 2502.87 → 2603.80 | +4.04% | 2550.44 → 2640.29 | 2.45% → 2.25% |
| 100-5-3 | 78 | 2928.86 → 2846.37 | -2.72% | 3043.39 → 2913.36 | 4.24% → 2.08% |
| 100-10-1_scenario02 | 161 | 2588.88 → 2617.72 | +1.11% | 2620.51 → 2665.94 | 1.06% → 2.82% |
| 100-10-1_scenario02 | 81 | 2624.12 → 2642.53 | +0.71% | 2699.67 → 2695.35 | 2.52% → 1.76% |

距离单位为 Zhou 坐标单位。没有大例最优值或可靠 gap，不能据此声明高质量解接近全局最优。
全部 20 组、60 对原始记录见 [完整报告](../../output/experiments/model5_alns_round4_20261001/report.md)，
质量与波动的 [对照图](../../output/experiments/model5_alns_round4_20261001/quality_and_dispersion.png) 同时保留退步。
最终 [保留决策](../../output/experiments/model5_alns_round4_20261001/retention_decision.json) 未按作业挑选最好结果。

## 当前入口与计时源码的关系

计时 `after/source` 是缓存开启的冻结候选；当前默认入口已关闭缓存，不能把本表作为默认升级的质量结果。
当前共享的 `evaluator.py` 增加了单批预处理的配送检查；M5 使用完整原配送检查。
并行单批工作曾在 `neighborhoods.py` 增加 `worst` 模式，随后撤回；M5 一直使用 `related/random`。
验收开始时与结束时的源码快照分别保留，未覆盖并行改动。
切换为实验参数前已验证三类紧池的 1000 次迭代轨迹、360 个随机解码及 60 次修复一致。
最终再验证默认模式对应冻结 `before`、实验模式对应冻结 `after`：三类案例、两种模式的
1000 次迭代轨迹、候选统计、时间表与独立审计均一致；最新共享文件再经 360 个随机解码与 60 次修复检查，见
[最终模式检查](../../output/experiments/model5_alns_round4_20261001/final_modes_verification.json)。
上述等价比较控制时钟，不保证真实墙钟截止下两次调用逐步相同。

活动测试覆盖默认关闭、实验开启和公开 M5 入口；本轮初测 17 项，最终入口专项 21 项通过。
早期仓库回归中两项精确模型稀疏度断言失败，其并行测试更新后的候选验收为 762 项通过。
最终默认/可选入口的仓库验收记录见下方测试日志。
最终默认/可选实现的全仓库回归 **763 项通过**，14 条现有 Matplotlib/Pyparsing 弃用警告。

## 复现及证据

当前公开 Python API 开启实验模式：

```python
from configuration.loader import load_routing_case, apply_container_pool_size
from models.routing.common.cycle import load_instance
from models.routing.variants.model_5 import solve_alns

config, fleet = load_routing_case("data/multi_batch/large/zhou_Set2_100-5-3/b3/case.json")
config, fleet = apply_container_pool_size(config, fleet, 78)
result = solve_alns(load_instance(config, fleet), max_time=15, max_iterations=100000,
                    seed=42, cache_descent_distances=True)
```

复跑表内缓存开启的冻结版本，写入新的阶段目录：

```sh
.venv/bin/python tools/audit/benchmark_model5_alns.py \
  --output output/experiments/model5_alns_round4_20261001 \
  --phase reproduce_after \
  --source output/experiments/model5_alns_round4_20261001/after/source
.venv/bin/python tools/audit/summarize_model5_alns.py \
  --output output/experiments/model5_alns_round4_20261001 --plot
.venv/bin/python tools/audit/verify_model5_benchmark.py \
  --output output/experiments/model5_alns_round4_20261001
```

[注册任务及输入哈希](../../output/experiments/model5_alns_round4_20261001/benchmark_manifest.json) ·
[枚举与搜索等价检查](../../output/experiments/model5_alns_round4_20261001/arc_equivalence.json) ·
[当前接口源码哈希](../../output/experiments/model5_alns_round4_20261001/current_m5_source_sha256.json) ·
[最终仓库测试](../../output/experiments/model5_alns_round4_20261001/pytest_final.log) ·
[最终重审计与统计核验](../../output/experiments/model5_alns_round4_20261001/verification_final.json)
