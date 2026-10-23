## Material Passport

- Origin Skill：academic-research-suite / experiment-agent
- Origin Mode：run，单案例机制试验与受控对照
- Origin Date：2026-10-22（Asia/Shanghai）
- Verification Status：VERIFIED（可行性与机制实例）；总成本优势未证实
- Version Label：m5_stock_chain_pilot_v1

# 第二轮尝试：库存关键链联合修复

**本轮验证了跨多个库存瓶颈的机制，但没有改善最终总成本。** 在 M02 上，新算子得到 5 箱可行方案，达到当前固定批次下的最小箱池；然而运输增加抵消了箱池节省，30 秒结果比本轮基线贵 0.83%，也未超过上一轮已经获得的更低成本方案。保留试验代码，默认关闭。

## 1. 问题与实现

继续使用分支 `codex/event-evaluation-pilot`。本轮开始时冻结了工作区已有代码，作为基线；保留上一轮的实现与结果。两个版本都关闭上一轮的 E2 日程缓存，只对照新参数 `critical_stock_repair=True` 的影响。

上一轮 M02 方案的各装载时点条件箱池需求为 **[5, 7, 7, 7]**。后三个时点同时达到最大值：仅改善一个时点，N 可能仍是 7，但为了提前回库增加的运输成本已经发生。因此，仅接受单步总成本改善的局部操作容易无法完成整条联动。

新增 `models/routing/model_5/alns/stock_chain.py`，执行以下过程：

1. 从实际一级回库时间、清洗完成时间重建每次装载的净库存需求，以当前 N−1 为修复目标。
2. 定位第一个尚未达到目标的装载时点，找到清洗太晚的容器及其二级送取路线、一级配送与回收批次。
3. 尝试解除相关等待、移出并重建最多 3 个完整送取请求、安排二级车辆，并联动一级送货顺序及回收车辆提示。完整送取覆盖保持不变。
4. 用原有完整评价器检查每个候选。对可行候选，按剩余库存缺口、所需箱池及总成本保留少量中间方案，允许缓解库存缺口但暂时增加成本的步骤。
5. **只把相对于起点总成本更低的完整方案交回主搜索**；新最优方案与最终结果仍经过独立审计。

限定搜索宽度 2、最多 4 层、每节点最多 32 个候选、每次最多 192 次评价。主循环每 150 次迭代最多触发一次，每次约 0.4 秒、累计约不超过总预算 15%；单次原子评价不能中途截断。重复起点会跳过，箱池达到必要下界后停止触发此修复。邻域和预算都是有限的，不构成完备搜索或最优性证明。

原有物理约束、箱池定价及独立审计模块没有修改。实测基线与候选仅有 `search.py`、`solver.py`、新增 `stock_chain.py` 三个模型文件不同，输入哈希相同。见 [源码与输入校验](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/stock_chain_pilot_20261022/verification.json)。

## 2. 本轮只跑一个正式案例

M02：20 客户、20 箱次、3 卫星、4 个固定日历段，每段均有配送与回收，批次配送量为 5、5、5、5。成本保持 `0.1 D1 + 0.05 D2 + 5 N + 0.02 Q`，采用项目现有合成单位。

- 正式对照仅 2 次完整求解：基线和候选各 30 秒，seed=42，迭代上限 10⁹，无外部热启动。
- 顺序为基线→候选，串行、独立子进程，`PYTHONHASHSEED=0`，数值库线程上限 1。
- 另做 1 次共同起点诊断：从上一轮已审计、成本 138.690672 的 M02 方案出发，单独调用新修复算子，最多 2 秒、192 次评价。它不混入 30 秒对照，也不作为候选版热启动。
- 所有正式结果都保留，未根据输赢调参重跑。另有用于正确性测试的构造小例子，不作为算法性能样本。
- 完整调用耗时分别为 30.014、30.017 秒；外部双周期审计不计入这段时间，求解器内部审计仍计入预算。

参数与环境见 [实验清单](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/stock_chain_pilot_20261022/experiment/manifest.json)；逐次结果见 [results.json](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/stock_chain_pilot_20261022/experiment/results.json)。

## 3. 同预算结果

| 指标 | 本轮基线 | 库存关键链版 |
| --- | ---: | ---: |
| 总成本 | 149.886063 | 151.136896 |
| 箱池 N | 7 | 5 |
| 一级距离 D1 | 957.792567 | 1068.584745 |
| 二级距离 D2 | 374.136134 | 377.568437 |
| 一级 / 二级趟数 | 7 / 13 | 8 / 14 |
| 迭代数 | 29,401 | 26,201 |
| 完整候选评价次数 | 26,363 | 24,321 |
| 进程 CPU 时间（秒） | 28.911 | 27.749 |
| 独立双周期审计 | 通过 | 通过 |

候选版成本增加 **1.250833，即 0.8345%**。分解为：少买 2 个箱子节省 10；一级距离成本增加 11.079218，二级距离成本增加 0.171615，其他项相同。因此“少用箱子”不能直接等同于“总成本更优”。

**上一轮已知方案仍然更好：N=7、成本 138.690672。** 本轮候选比该历史结果高约 8.97%。历史方案不是本轮同进程、同时段的对照，不能拿它替换本轮基线；但必须将其列出，避免误把较弱基线上的表现当成超过已有最好结果。

两版限时搜索都依赖墙钟进度，轨迹与实际 CPU 时间不同。本轮没有为查明基线波动而追加种子或重复求解，因此不能从一次差值推断期望性能，也不能将迭代数减少全部归因于算子开销。

### 新算子在运行中确实跨过了成本障碍

新算子实际调用 4 次，评价 559 个候选，其中 424 个通过完整评价；累计耗时 0.596 秒，占 30 秒预算约 1.99%。产生 1 次经独立审计接受的新最优解。

该次调用的起点为 N=7、成本 157.499245，库存需求 [5, 7, 7, 7]。沿以下路径完成了三步联动：

| 步骤 | 主要卫星 / 回收批次 | N | 总成本 | 相对目标 N=6 的剩余缺口总和 |
| --- | --- | ---: | ---: | ---: |
| 1 | S8 / b1 | 7 | 168.505696 | 2 |
| 2 | S8 / b2 | 7 | 169.742424 | 1 |
| 3 | S4 / b3 | 5 | 154.535448 | 0 |

前两步成本上升且 N 未变，第三步 N 降至 5，总成本低于该次调用起点，故被接受。随后原有搜索继续把成本降至 151.136896。这是一次实际运行中观测到的机制实例；它不证明最终结果优于另一条搜索轨迹。

### 箱池数量下界已在这个案例上达到

每批在同一装载事件提取 5 个干净箱子，故固定批次下必有 N≥5。候选版给出了 N=5 的完整可行周期，并通过双周期审计，所以 **M02 当前固定日历、固定订单批次下的最小箱池是 5**。

这里证明的是箱池数量的最小值，不是包含运输费用的总成本最优值。上一轮 N=7 的更便宜方案正好说明这两件事不同。该结论也不能直接套用到改变订单批次与日历的 M6–M8。

## 4. 从同一个较好方案出发的诊断

历史参考方案经本轮评价器重建后，成本仍为 138.690672，避免了不同起点造成的歧义。修复实际用时 0.148 秒，评价 160 个候选、106 个可行候选，达到第 3 层。

| 方案 | N | 成本 | 处理 |
| --- | ---: | ---: | --- |
| 共同起点 | 7 | 138.690672 | 保留 |
| 本次修复发现的最小箱池方案 | 5 | 151.375673 | 因总成本增加而拒绝 |

这个 5 箱方案也单独通过了双周期审计。与共同起点相比，运输费用增加 22.685000，而箱池费用仅节省 10，所以总成本增加 12.685000。单看这两个固定方案，箱池单位价格需高于约 **11.3425** 才会改变二者的成本排序；当前价格是 5。这个数只是两份既有方案的代数交点，没有重新优化价格情景，也不是所有方案的全局临界价格。

诊断原始记录见 [共同起点结果](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/stock_chain_pilot_20261022/experiment/mechanism/candidate/summary.json)、[低箱池方案独立审计](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/stock_chain_pilot_20261022/experiment/mechanism/candidate/lowest_pool_audit.json) 和 [全部候选评价记录](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/stock_chain_pilot_20261022/experiment/mechanism/candidate/evaluation_trace.json)。

## 5. 这次学到了什么，下一次改什么

这轮比简单日程缓存提供了更明确的结构证据：多个装载时点同时决定 N，确实可能需要连续跨越数个成本暂时上升的步骤；一个有限宽度的联合修复可以找到这种路径。

当前主要限制在于内部筛选偏向消除库存缺口，且通过拆出独立送取路线、改变一级供货与回收车辆来换取提前清洗，可能显著增加运输。外层按完整成本拒绝劣解是有效的，但内部仍可能把预算集中在“箱池更小、运输更贵”的区域。

下一步优先做**成本导向的链修复**：在各层保留库存缺口与运输费用之间的多种不互相支配的方案，允许保留 N=6、N=7 的低运输成本分支，并加入合并一级配送/回收路线的动作。其目标应是低于已知成本 138.690672，而不是单纯再次找到 N=5。该方案尚未实现或验证，不属于本轮成果。

本轮维持 `critical_stock_repair=False`。没有扩展到 M6、M7、M8，也没有执行完整数据集；继续保留完整原始结果，便于下一轮使用同一较好起点比较。

## 6. 验证与限制

- 专项测试：**29 passed**。包含真实双峰库存构造例子（第一步成本上涨、第二步 N 从 4 降到 3 且总成本下降）、只允许一层时不返回更贵中间解、截止时间与评价次数限制、达到下界不再搜索、趟数限制、清洗与装载同刻可复用、送取覆盖及输入不被修改，以及此前缓存与局部改进回归。
- 基础检查：**91 passed、15 skipped**。跳过项因本机 Gurobi 许可证于 2026-10-21 到期，未计作通过；本轮 ALNS 和独立审计正常。14 条依赖弃用告警来自现有 Matplotlib / Pyparsing。
- 当前求解器文件与已测候选快照逐文件哈希一致，`git diff --check` 通过。
- 单案例、单种子不支持显著性结论。没有精确总成本最优参照，不报告最优性 gap；达到 N 下界不代表总成本已最优。
- 本轮两次正式求解及一次独立机制诊断全部成功退出；没有删去失败的算法结果或以更有利的重复运行替换结果。

测试命令和输出见 [测试清单](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/stock_chain_pilot_20261022/tests/summary.json)、[专项日志](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/stock_chain_pilot_20261022/tests/targeted.log)、[基础检查与跳过原因](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/stock_chain_pilot_20261022/tests/smoke.log)。

## 7. 材料与复现

- [简明对照 CSV](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/stock_chain_pilot_20261022/comparison.csv)
- [本轮实现补丁（不包含上一轮修改）](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/stock_chain_pilot_20261022/round2_implementation.patch)
- [冻结的两版源码及 M02 输入](/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/stock_chain_pilot_20261022/experiment/sources.zip)
- 正式结果和审计：`experiment/quality/<baseline|candidate>/`。
- 历史共同起点副本：`experiment/inputs/anchor_result.json`，其哈希已写入清单。
- 实验驱动：`experiments/runs/pilot_m5_stock_chain.py`；原驱动副本及冻结辅助脚本同时保存在实验目录。

在项目根目录使用新输出目录复现：

```sh
.venv/bin/python -m zipfile -e output/stock_chain_pilot_20261022/experiment/sources.zip /private/tmp/m5_stock_chain_restore
.venv/bin/python experiments/runs/pilot_m5_stock_chain.py \
  --baseline-source /private/tmp/m5_stock_chain_restore/sources/baseline \
  --source /private/tmp/m5_stock_chain_restore/sources/candidate \
  --reference output/stock_chain_pilot_20261022/experiment/inputs/anchor_result.json \
  --seconds 30 --seed 42 \
  --output output/stock_chain_pilot_recheck
```

限时搜索的最终成本和计数可能随机器负载改变。只复核共同起点机制时，可调用同一驱动的 `--worker mechanism --variant candidate`，使用上述冻结候选源码与参考方案，并指定新的输出目录。
