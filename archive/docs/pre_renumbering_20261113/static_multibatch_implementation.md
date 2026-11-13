**静态多批次代码与实验入口**

版本：`static_multibatch_study_v1`，2026-09-29。业务策略仍为 `multi_batch_cutoff_v1`。
依据：[静态研究计划](static_single_multibatch_study_plan.md)。

订单全周期提前已知，`delivery_batch_id` 和各批日程固定。求解器优化两级路线和实际作业时间，
由二级卸箱完成时刻决定回运轮次；总仓卸箱及清洗完成后才释放容器。
一级每车每批最多一趟，二级使用有限物理车队并可跨批多趟执行，末态恢复全池清洁归仓。
固定 N 时目标为两级总距离。B=1 的固定分仓循环为 M3，B>1 为 M5，二者复用共同约束机制。

**单次运行**

在项目根目录执行：

```sh
.venv/bin/python main.py route \
  --case data/multi_batch/small/mechanism_forced_late/b3/case.json \
  --solver alns --time 2

.venv/bin/python main.py route \
  --case data/multi_batch/small/mechanism_forced_late/b3/case.json \
  --solver exact --exact-time 30
```

精确求解需要有效 Gurobi 许可证；ALNS 和解析必要条件检查不依赖 Gurobi。
输入结构、装卸规则和时间单位见 [多批次使用说明](model5_multibatch_cycle.md)。

**静态研究批量运行**

```sh
# 校验文件哈希、输入字段与配对条件，然后列出运行矩阵；不求解、不创建结果目录
.venv/bin/python experiments/runs/run_multibatch_cycle.py \
  --dataset-manifest data/manifest.json \
  --families mechanism_forced_late mechanism_cutoff_choice \
  --batch-counts 3 --suggested-pools --solver alns --dry-run

# 同订单 B=1/2/3、相同测试池、一个精确参考及三个 ALNS 种子
.venv/bin/python experiments/runs/run_multibatch_cycle.py \
  --dataset-manifest data/manifest.json \
  --families mechanism_forced_late mechanism_cutoff_choice \
  --pool-sizes 2 3 6 --seeds 42 43 44 --time 2 --exact-time 30

# 固定分批下的清洗时长敏感性；每个清洗值作为独立场景
.venv/bin/python experiments/runs/run_multibatch_cycle.py \
  --dataset-manifest data/manifest.json \
  --families mechanism_forced_late --batch-counts 3 \
  --pool-sizes 2 3 6 --cleaning-durations 5 20 --solver alns --time 2
```

还可按 `--groups single_batch multi_batch`、`--sizes small medium large` 筛选。
来源用独立的 `--source-kinds synthetic_mechanism zhou_derived_scenario lade_derived_scenario` 筛选，LaDe 的 B1 也属于单批次类别。
`--suggested-pools` 使用各案例 manifest 声明的池规模网格，与 `--pool-sizes` 互斥。
建议网格只是选点，不承诺能覆盖最小可行 N。想比较单批/多批时，应显式提供共同 N。
`--iterations` 可限制 ALNS 迭代次数，`--seeds` 控制随机种子。
ALNS 同时受迭代与时间上限约束，先达到任一上限即结束。
批量测试可加 `--no-plots` 跳过逐次 PNG 绘图，仍完整保存日程、账本与汇总表；
单次案例也可在配置中设置 `save_plots: false`。绘图时间计入外层墙钟上限。

运行前写出完整任务、种子、预算与哈希，复制全部输入和执行源码；子进程从冻结副本运行。
每个子进程外层上限为求解预算加 `--timeout-overhead`（默认 30 秒）。
成功、必要条件拒绝、MILP 不可行、未找到解、超时及异常均保留，不自动补跑失败项。
`--output` 只接受新目录或空目录。

**新增检查和初始构造**

- `precheck.py` 检查不可拆分订单/前置仓配送的容量、一级总运力、最早服务、最终截止、
  清洗与车辆末态，并计算逐批领箱的必要池下界。
- 时间下界使用放宽路网上的最短路，允许忽略竞争、节点访问资格与中间服务时间。
  这提供乐观下界，对非对称或不满足三角不等式的输入矩阵也不会把直连路程错当最短时间。
- 第 b 批的必要池下界为：截至该批累计领箱量，减去此前订单中理论上可能在本批前洗好的箱次，
  并至少达到本批需求。清洗恰好在领箱时完成可用。
- 必要条件失败产生 `INFEASIBLE`、`proven_infeasible=true` 和逐项证据；通过仅表示未排除可行性。
  ALNS 搜索失败仍返回 `HEURISTIC_NO_SOLUTION`，不会据此宣称不可行。
- 长周期 B=1 的装载初解可能在完成晚期配送前错过早期回收；增加按时间顺序逐单送取的备选构造，
  完整检查车队占用、趟次和全部业务约束。不会修改订单窗口或配送批次。
- 加载器读取前置仓声明的 `service_time`；ALNS 解码器用拓扑顺序一次传播作业时间和车辆复用约束。

**结果与证据口径**

| 文件 | 内容 |
| --- | --- |
| `manifest.json`、`inputs/`、`source_snapshot/` | 完整运行矩阵、输入和可执行源码快照 |
| `runs.csv`、`report.md` | 所有尝试、状态、审计、距离、界、gap、证明范围、工时及车辆数 |
| `pool_summary.json/csv`、`study_report.md` | 池下界、最小可行测试池、已得时间表最小池、最小池证明及未解决点 |
| `algorithm_summary.csv` | 同案例同池的算法可行率、有效解距离及 gap；所有失败保留在分母 |
| `batch_comparisons.csv` | 同家族、池、清洗时长、算法及种子的 B=1/B>1 对照；缺失基线为 NOT_RUN |
| `clean_stock.csv`、`cutoff_inventory.csv` | 有效解逐批清洁库存与各仓冻结量 |
| 每次运行的 `result.json` | 实际路线、整数 ticks 日程、独立审计、守恒账本及连续两周期重放 |

只有有效解与必要池下界闭合，才填写 `proven_minimum_pool`。
同场景 N 增大保持原解可行，因此已证明某 N 不可行可以排除全部更小 N。
时间表最小池单独报告，不把启发式搜索得到的最小测试 N 自动当作全问题最优。
若有效解与下界证据冲突，报告冲突并取消最小池证明。

这些入口完成静态模型的可运行研究链路。大规模算法性能、真实回收参数校准及经济性对照，
仍需各自声明的数据、预算与正式实验；代码验证结果不能替代这些研究结论。

**回归检查**

```sh
.venv/bin/python -m pytest tests/routing/test_multibatch_cycle.py \
  tests/routing/test_static_multibatch_study.py -q
```

新增测试覆盖解析池下界、清洗等号、最终截止、非度量时间矩阵、无需 Gurobi 的解析拒绝、
前置仓服务时间、长周期初解、哈希拒绝、运行矩阵、外层超时、冻结副本执行及证明边界。

2026-09-29 验证：上述两个路由测试文件加 `tests/data/test_static_batch_suite.py` 共 **88 passed**，
其中新增静态研究测试 28 项；Gurobi 测试实际执行。保留 14 条既有 Matplotlib/PyParsing 弃用警告。

[端到端机制验证](../output/experiments/static_batch_suite_v1/code_validation_20260929/study_report.md)
运行两个六订单家族的 B=1/B=3、N=2/3/6，精确求解预算 5 秒、ALNS 预算 0.5 秒、种子 42。
24 次尝试中，14 个解通过独立审计与连续两周期重放，10 次以必要条件证明不可行。
两个 B=1 的最小池均为 6；三批次强制迟回例最小池为 3，截止可调整例为 2。
7 个有效精确解均证明距离最优；7 个 ALNS 解相对对应最优值的 gap 为 0 至约 4.2361%。
这些是实现与机制证据；各规模数据的入口测试使用短预算初始构造，不构成正式性能评测。

2026-09-29 单批扩展：B=1 已接入 M1–M4 及条件经济性链路，详见 [单批实施说明](single_batch_comparison.md)。多批次编号为 M5，当前仅固定分仓。
