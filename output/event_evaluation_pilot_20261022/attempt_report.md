## Material Passport

- Origin Skill：academic-research-suite / experiment-agent
- Origin Mode：run；小范围算法工程试验与结果核查
- Origin Date：2026-10-22（Asia/Shanghai）
- Verification Status：VERIFIED（候选重放一致性与最终物理审计）；性能优势未证实
- Version Label：m5_event_cache_pilot_v1

# 模型 5 二级事件日程缓存：尝试报告

**结论：实现可用于继续研究，但本轮没有观察到足够稳定、足够大的收益，保留默认关闭。**
只试了 M02、L06 两个算例。每版每例 30 秒、seed=42，M02 总成本相同，L06 总成本下降约 0.027%；迭代吞吐变化均不到 1%。因此不能据此宣布算法明显改进，也暂不把这一缓存直接推广至模型 6、7、8。

## 1. 本轮具体尝试了什么

分支：`codex/event-evaluation-pilot`。基线提交：`2c01a8a8ad07386d7c5664c5ed7f0352e018ba20`。
试验前工作区干净；基线从该提交冻结，候选版在同一基础上修改。

这轮检验的是此前讨论中的**安全复用事件计算**，具体实现为“整个二级耦合事件日程缓存”。ALNS 的邻域、算子权重、退火、重启和目标函数保持原有定义；这不是完整复现文献中的 forward time slack，也不是新增同步破坏算子的实验。

缓存以一次求解中的固定问题实例为边界，最多保留 128 个条目。键包含全部非空二级路线的卫星、车辆、任务顺序、出发等待、回库等待，以及一级配送在各卫星、各批次的实际到货完成时间。只有这些输入全部相同才复用二级日程。客户送取先后关系和同车多趟衔接被作为整体保留，未将相互依赖的路线单独缓存。

每次评价仍重新进行回库截点归属、一级回收调度、清洗完成与可用库存计算；一级回收解码继续使用原有的精确输入缓存。成本评价、候选最优解检查和最终独立双周期审计继续执行。返回缓存结果时复制可变记录，避免调用者修改污染缓存。

入口为 `models.routing.model_5.alns.solver.solve_alns(..., cache_e2_schedule=True)`；默认 `False`，仅用于 M5 多批次。物理约束、费用和独立审计模块没有改动。缓存建键、复制仍随事件数增长，**没有常数时间复杂度的主张**。

## 2. 实验范围与控制

| 算例 | 客户数 / 需求箱次 Q | 卫星数 | 固定日历段数 K / 实际配送批次数 | 各批需求 |
| --- | ---: | ---: | ---: | --- |
| M02 | 20 / 20 | 3 | 4 / 4 | 5、5、5、5 |
| L06 | 100 / 100 | 5 | 3 / 3 | 20、50、30 |

数据为 `formal_benchmark27_spacetime_v2`，两个案例在运行前选定。目标为项目现有合成成本 `0.1 D1 + 0.05 D2 + 5 N + 0.02 Q`，不解释为真实货币。

- 正式质量对照：2 算例 × 2 版本，共 4 次求解；每次 30 秒，迭代上限 10⁹，seed=42，无外部热启动。
- 所有实验子进程串行运行，`PYTHONHASHSEED=0`，数值库线程上限为 1。M02 先基线后候选，L06 次序相反。
- 前置剖析：每例原版 5 秒 cProfile；用于定位，未把剖析结果混入质量对照。
- 等价性与速度分离：从原版每例取前 600 次实际候选评价输入，连同原版完整输出的规范化 JSON 哈希保存。两版本分别重放相同序列 3 遍，计时不含哈希计算。每次从新进程和空求解缓存开始。
- 发现短时墙钟计时波动后，只对同一批候选补测 1 轮进程 CPU 时间。没有新增算例、随机种子或质量求解。该补测属于事后诊断，不替代预先执行的结果。
- 最终外部双周期审计在求解计时之外；求解器内部原有的审计仍计入预算。实测完整调用为 30.013–30.027 秒，含入口及收尾开销。

源码、所选输入、驱动、环境和哈希均已冻结；两版本的数据文件哈希相同，当前模型代码与实测候选快照一致。原始信息见 [实验清单](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/event_evaluation_pilot_20261022/experiment/manifest.json)、[完整结果](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/event_evaluation_pilot_20261022/experiment/results.json) 和 [校验记录](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/event_evaluation_pilot_20261022/verification.json)。

## 3. 结果

### 同预算解质量

| 算例 | 原版成本 | 缓存版成本 | 成本降低比例 | 箱池 N | 独立双周期审计 |
| --- | ---: | ---: | ---: | ---: | --- |
| M02 | 138.690672 | 138.690672 | 0.0000% | 7 → 7 | 两版均通过 |
| L06 | 409.870442 | 409.760941 | 0.0267% | 50 → 50 | 两版均通过 |

L06 的改善量仅为 0.109501 合成成本单位，来自一级距离费用减少 0.119388、二级距离费用增加 0.009887；箱池成本没有变化。两例都只得到启发式可行解，没有证明总成本全局最优。

### 搜索吞吐与资源

| 算例 | 迭代数：原版 → 缓存版 | 变化 | 完整候选评价次数：原版 → 缓存版 | 缓存版 E2 命中率 |
| --- | ---: | ---: | ---: | ---: |
| M02 | 29,793 → 29,518 | -0.92% | 26,548 → 26,333 | 27.45% |
| L06 | 8,824 → 8,908 | +0.95% | 8,464 → 8,538 | 36.91% |

| 算例 | 原版进程峰值 RSS（MiB） | 缓存版峰值 RSS（MiB） | 差值 |
| --- | ---: | ---: | ---: |
| M02 | 37.75 | 39.53 | +1.78 |
| L06 | 69.55 | 80.05 | +10.50 |

这里的 RSS 是整个进程峰值，不是缓存对象单独占用量。迭代数与完整评价次数不同，原有整状态缓存和无效操作跳过会影响计数。

### 同一候选序列的重放

两例各 600 个候选，**1,200 个记录均与改动前原版的评分、日程、成本信息和违规列表逐项等价**。原版和候选各 3 轮、补充 CPU 一轮均为零哈希差异。M02 序列含 481 个无违规候选，L06 为 421 个，其余为不可行候选；测试没有只保留可行状态。

固定序列的 E2 命中率分别是 M02 47.00%、L06 19.83%，与上表 30 秒完整搜索的命中率不同，不能混用。

| 算例 | 版本 | 前三轮墙钟时间（秒） | 中位数 | 补测墙钟（秒） | 补测进程 CPU（秒） |
| --- | --- | --- | ---: | ---: | ---: |
| M02 | baseline | 0.6636 / 1.7005 / 0.6705 | 0.6705 | 0.6552 | 0.6550 |
| M02 | candidate | 1.6339 / 0.6232 / 1.6508 | 1.6339 | 1.5985 | 0.7287 |
| L06 | baseline | 3.2049 / 3.1422 / 3.1562 | 3.1562 | 3.1898 | 2.2276 |
| L06 | candidate | 5.3963 / 3.1544 / 3.1091 | 3.1544 | 3.1122 | 2.1782 |

前三轮 M02 的计时在约 0.6–1.7 秒之间波动，L06 候选也出现 5.40 秒的观测。补测中部分墙钟时间比本进程 CPU 时间多约 0.9 秒，具体原因未定位，不能把这些差额直接归因于缓存实现。补测 CPU 时间在 M02 约增加 11.25%、L06 约减少 2.22%，且各只有一次，仍不足以证明稳定加速。原始数据见 [CPU 补测](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/event_evaluation_pilot_20261022/cpu_timing_check/results.json)。

## 4. 为什么这版的收益有限

基线剖析中，候选 `evaluate` 累计耗时约占调用总耗时的 76%–84%；物理 `_audit` 约占 41%–43%。后者包含在前者及最终检查中，比例不可相加。缓存仅省去部分二级解码，完整审计和库存计算仍然保留，因此有明显的收益上限。相关剖析见 [M02 profile](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/event_evaluation_pilot_20261022/profile_ready/candidate/m5_M02/profile.txt) 和 [L06 profile](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/event_evaluation_pilot_20261022/profile_ready/candidate/m5_L06/profile.txt)。

从代码结构可以解释的限制是：任何一条二级路线或一级到货时间变化，都会使整个二级日程键变化；命中时仍需要建立键和复制事件记录。命中率因阶段和算例而异，不能保证覆盖这些额外开销。这是对当前实现的机制分析，尚未通过消融实验量化每种开销。

这次小试只支持“当前缓存粒度没有显示出实用优势”，不能推出“事件增量计算无效”或“同步感知 ALNS 无效”。没有测试按卫星划分的事件子图缓存、时间余量更新、库存关键链破坏修复，也没有比较其他启发式算法。

## 5. 对下一步算法选择的具体影响

**M02 更适合作为下一次库存联动算子的试验案例。** 当前 N=7，而乐观必要条件下界为 5，存在尚未闭合的差距；这不意味着 N=5 或 N=6 一定可行。下一轮可以只做一个算子：定位决定箱池需求的装载时点，回溯相关的二级回库、一级回收与清洗事件，联合重排少量任务，再用完整成本评价。减少 1 个箱子的成本收益为 5，只有新增运输成本低于收益且所有时间约束满足，才是有效改进。

**L06 在当前 M5 固定日历和订单批次下，箱池已经达到下界。** 第二批一次装载 50 箱，必有 N≥50；现有审计可行解使用 50 箱。因此对这组固定批次，箱池数量的最小值已达到，但整体总成本仍未证最优。继续做以降低 N 为目标的固定批次移动没有空间，应优先检验路线距离与车辆衔接的联合改进。这个下界不直接适用于会重新分配批次的 M6–M8。证据见 [必要条件与逐订单时间下界](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/event_evaluation_pilot_20261022/necessary_conditions.json)。

模型 6–8 若将来使用事件缓存，需要把订单批次分配、K、截点和段长纳入失效条件，或每个投影实例使用独立缓存。尤其当回库跨截点、清洗跨下一次装载边界时，必须重算离散批次归属及库存。本轮没有改动这些模型。

建议保留本分支作为可复现试验，**不把当前整块二级日程缓存设为默认优化**。下一次仍先用 M02 一个案例验证库存关键链算子，无需马上扩到全部案例。

## 6. 验证、异常和结论边界

- `make test`：91 passed、15 skipped，14 条既有 Matplotlib / Pyparsing 弃用告警。
- 与本改动相关的专项组合及 M5 箱池检查（`-k 'not exact'`）：62 passed、2 skipped、16 deselected。测试涵盖截点相等与晚一刻度导致 N 从 1 变 2、供应时间变化、出发/回库等待、车辆非法、任务重复、时间依赖成环、缓存记录被调用者修改、一级回收改变后的库存重算、固定迭代解一致性。
- 跳过的检查依赖 Gurobi 初始化；探测到本机许可证于 2026-10-21 到期。跳过项不计为通过。本轮 ALNS 的四次求解和全部独立审计正常完成，未运行精确最优对照。日志见 [测试记录](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/event_evaluation_pilot_20261022/tests/summary.json)、[许可证探测](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/event_evaluation_pilot_20261022/tests/gurobi_availability.txt)。
- 初次性能剖析因冻结源码目录缺少所选数据而报 `FileNotFoundError`；补齐两案例输入后重跑，原失败日志保留在 `profile/`。该失败没有产生可比较的求解结果。后续正式实验全部完成。
- 新驱动最后仅增添了 CPU 计时字段；正式实验原驱动保存在 `experiment/driver.py`，补测驱动在 `cpu_timing_check/driver.py`，求解器代码未再改变。
- 两个案例、单随机种子不能支持显著性或普适优越性结论。重复同一候选序列的三次计时不是三个独立算法样本。限时退火依赖墙钟进度，seed 相同也不保证两版本走相同轨迹；0.027% 的成本差异不应解释为已证实的算法优势。
- 没有最优值对照，故不报告最优性 gap；未进行多模型或完整 27 案例实验。

## 7. 文件与复现

- 阅读用对照表：[comparison.csv](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/event_evaluation_pilot_20261022/comparison.csv)。
- 基线与候选源码、所选输入的压缩快照：[sources.zip](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/event_evaluation_pilot_20261022/experiment/sources.zip)；逐文件 SHA-256 在实验清单中。
- 本次代码、测试及文档改动：[implementation.patch](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/event_evaluation_pilot_20261022/implementation.patch)。
- 四次完整方案与独立审计在 `experiment/solve/<M02|L06>/<baseline|candidate>/`；原始候选序列在 `experiment/trace/<case>/baseline/trace.json.gz`。
- 原始结果、哈希和测试日志均保留，没有用后续更好运行替换本轮结果。

在项目根目录解压冻结源码，并用新目录复现小范围对照：

```sh
.venv/bin/python -m zipfile -e output/event_evaluation_pilot_20261022/experiment/sources.zip /private/tmp/m5_event_pilot_restore
.venv/bin/python experiments/runs/pilot_m5_event_cache.py \
  --baseline-source /private/tmp/m5_event_pilot_restore/sources/baseline \
  --source /private/tmp/m5_event_pilot_restore/sources/candidate \
  --cases M02 L06 --seconds 30 --seed 42 \
  --trace-limit 600 --replay-rounds 3 \
  --output output/event_evaluation_pilot_recheck
```

限时结果受机器和系统负载影响，不能保证最终数值逐位重现。若只复核语义一致性，使用保存的候选序列：

```sh
.venv/bin/python experiments/runs/pilot_m5_event_cache.py \
  --worker replay --variant candidate --case M02 \
  --source /private/tmp/m5_event_pilot_restore/sources/candidate \
  --trace output/event_evaluation_pilot_20261022/experiment/trace/M02/baseline/trace.json.gz \
  --output output/event_evaluation_pilot_replay_M02
```

将 M02 替换为 L06 可检查第二例。重放结果的 `identical=true` 表示全部候选输出与本轮保存的原版哈希一致；耗时可不同。
