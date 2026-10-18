# 实验

这里存放实验程序：`runs/` 负责批量运行，`analysis/` 负责分析汇总。
实验参数位于 `config/experiments/`，生成的结果、图片、报告和 Excel 统一保存到 `output/`。
例如 `experiments/runs/run_single_batch_benchmark.py` 是程序，
`output/experiments/m1_m4_comparison_20261004/` 是一次实验的成果。

## 按任务选择入口

| 任务 | 运行模块 | 结果 / 说明 |
| --- | --- | --- |
| 正式27例八模型、精确与多种子ALNS | `experiments.runs.run_formal_benchmark27` | [冻结、恢复与结果口径](../docs/benchmark27_remote_run.md) |
| M6–M8 求解改进的同机成对诊断 | `experiments.runs.compare_joint_refinement` | [有限预算验证（选例）](../output/inspections/solver_improvement_20261017/report.md) |
| 单批四模型机制与经济对照 | `experiments.runs.run_single_batch_comparison` | [四模型说明](../docs/single_batch_comparison.md) |
| M1–M4 全规模算法比较 | `experiments.runs.run_single_batch_benchmark` | [预算与结果](../output/experiments/m1_m4_comparison_20261004/m1_m4_solver_comparison_20261004.md) |
| 固定批次与池规模扫描 | `experiments.runs.run_multibatch_cycle` | [使用说明](../docs/static_multibatch_implementation.md) |
| M5 全规模算法比较 | `experiments.runs.run_model5_benchmark` | [预算与结果](../output/experiments/m5_comparison_20261004/m5_solver_comparison_20261004.md) |
| M5/M6 共享数据比较 | `experiments.runs.run_model5_model6_shared` | [数据与比较口径](../docs/model5_model6_shared_dataset_20261005.md) |
| M6 等长批次与共同分界 | `experiments.runs.run_model6_uniform_comparison` | [模型设计](../docs/model6_joint_batch_cost_design.md) |
| M6–M8 日历审查 | `tools.audit.benchmark_model78` | [测评报告](../output/inspections/model78_review_20261005/model78_review_20261005.md) |
| 论文 E0 / E1a 冻结与恢复 | `experiments.runs.run_thesis_completion` | [执行计划与阶段记录](../docs/thesis_experiment_completion_plan_20261012.md) |
| 运输 / 容器成本比例实验 | `experiments.runs.run_cost_ratio_suite` | [比例测试](../docs/transport_container_ratio_tests_v1.md) |
| 已保存方案的经济重计价 | `experiments.analysis.recalculate_economics` | [重新核算](../output/economic_recalculation_20261005/economic_recalculation_20261005.md) |

从仓库根目录使用 `.venv/bin/python -m 模块名 --help` 查看参数；新的运行写入新目录。
M3/M5/M6/M7/M8 最新成本对比的逐轮专属脚本及复现步骤见[结果索引](../output/model35678_comparison/README.md)。
这些冻结实验脚本维持原路径。通用验证、源码检索与恢复方法见[开发指南](../docs/development.md)。

## 论文补充实验驱动

先按项目运行环境约定预检，再用项目解释器运行以下模块。`prepare` 冻结源码 ZIP、逐文件哈希、输入、完整命令、机器与并发设置；执行时使用实验目录里的冻结 `driver.py`。`--baseline` 指向修改前的源码快照。`e0` 固定 12 例、3 模型、精确 300 秒及 ALNS 五种子各 30 秒；`e1a` 为 3 例、3 模型、两版本、五种子、300 秒；`e1exact` 为 3 例、3 模型、两版本、单种子、300 秒。

```sh
.venv/bin/python -m tools.data.build_thesis_completion_suite --output config/cases/thesis_completion/new_pilot
.venv/bin/python -m experiments.runs.run_thesis_completion prepare --phase smoke --suite config/cases/thesis_completion/new_pilot/manifest.json --baseline /path/to/original/source_snapshot --output output/thesis_completion/new_smoke
.venv/bin/python output/thesis_completion/new_smoke/driver.py run --output output/thesis_completion/new_smoke
.venv/bin/python -m experiments.runs.run_thesis_completion status --output output/thesis_completion/new_smoke
```

正式 `prepare` 须用 `--overhead-basis` 记录短预算试跑测得的外层启动/收尾开销，`--timeout-overhead` 给出登记宽限。`--full-k-domain` 用于 E1a 的同模型新旧策略对照；E0 保持共同 K={1,2,3} 和整数时钟。新旧策略隔离检查只允许六个 `batch_search.py` 的求解代码有差异。

重复运行 `run` 会校验哈希并跳过完整任务；`--retry-failed` 为错误、外层超时或中断另建 attempt，首轮分母保持不变。`restore` 从校验过的 ZIP 恢复未随 Git 重复保存的源码副本，拒绝覆盖修改过的文件。`analyze` 生成逐种子的参照差异和阶段门槛，区分精确最优值与仅可行参照。`pipeline --output <队列目录> --studies <阶段目录...>` 顺序执行已冻结阶段，遇到执行错误、审计或证书矛盾时停在该阶段。运行目录中的 `STOP` 文件使当前阶段在活动子进程结束后停下；队列目录的 `STOP` 在下一阶段前生效。

`count_search` 保留每个 K 一行，`count_search_visits` 保留逐轮访问；`anytime.csv` 对两版使用同一外层观察器，在下一候选开始或全调用返回时确认已处理候选的最好可行费用，不把局部历史提前记成全局发现。原生细粒度轨迹仍保存在新版 `result.json`，逐 K 建模和搜索诊断另存 `candidate_diagnostics.json`。`summary.csv` 包含全部计划任务，`attempts.csv` 保留每一次尝试；完成状态不代表所有任务可行或最优。

## 模型6均匀周期基础版（2026-10-05）

`runs/run_model6_uniform_comparison.py`比较M5原固定日历与M6均匀周期模型，逐次冻结源码与输入、独立审计并保存所有超时和失败。M6的一次总预算在配置K之间分配，各批共用分界；结果分别显示K、箱数、配送批次、实际回运轮数和统一两阶段时长。只有所有K解决才报告整体成本最优。数据生成器用`--uniform-cycle --batch-count-candidates 1 2 3`启用。

    .venv/bin/python -m experiments.runs.run_model6_uniform_comparison --manifest data/model6/uniform_v1_20261005/manifest.json --exact-time 24 --time 10 --iterations 5000 --seeds 42 43 --workers 2 --output /path/to/new/uniform_study

求解实现修改前后可用 `runs/review_model6_search.py` 配对检查。`--before-source` 指向历史运行保存的源码快照，当前源码作为after；输入、费用、预算和种子相同，串行执行且交替两版先后顺序。输出全部原始结果、独立审计、源码/输入哈希和逐种子配对表，保留未找到解的尝试。该检查属于短预算描述性验证，不代表收敛、显著性或真实货币收益。

    .venv/bin/python -m experiments.runs.review_model6_search --manifest data/model6/uniform_v1_20261005/manifest.json --before-source output/model6_comparison/20261005_uniform_v1_final/source_snapshot --output /path/to/new/search_review --exact-time 24 --time 10 --iterations 5000 --seeds 42 43

该入口作短预算工程验证。两模型的原日历/优化日历和目标函数同时变化，报告箱数对比而不把差异全归因于均匀分批。不做统计显著、性能收敛或真实人民币节约结论。

正式入口为 `runs/run_multibatch_cycle.py`，汇总在 `analysis/summarize_multibatch_cycle.py` 与 `analysis/summarize_static_multibatch.py`。
旧单日 runner 保留用于显式历史基线，不能替代新版 B=1。
其余旧模型实验已归档，见 `../archive/cleanup_20260929/`。

`runs/run_single_batch_comparison.py` 是新版四模型 B=1 与条件经济对照入口，支持冻结输入、独立外层超时、全部失败留存和按方法/种子分别核算。

`runs/run_single_batch_benchmark.py` 运行当前 M1–M4 的精确/ALNS 全量规模对比。
`analysis/summarize_single_batch_models.py --output <实验目录>` 读取其已保存结果，生成四模型横向对比，
列出 M2 相对 M1、M4 相对 M3 的距离减少量和减少率。使用 `--watch` 可在求解期间持续更新，
全部求解完成后自动收尾。精确可行解、ALNS 三种子最好和三种子均值分开报告；
汇总采用同一组四模型完整可行案例，并保留负减少率及缺失状态。

`runs/run_model5_benchmark.py` 补测 M5 的 B2/B3 精确算法与独立 ALNS，默认覆盖 140 个多批次配置、560 次运行。
支持 `--prepare-only` 冻结登记、`--resume` 校验快照后恢复，以及 `--after-progress` 等待前一轮实验完成。
逐规模和批次数分别输出 `runs.csv`、`comparison.csv`、`summary.csv` 与动态 `report.md`。
正式预算与复现说明见 [M5 全规模补测](../output/experiments/m5_comparison_20261004/m5_solver_comparison_20261004.md)。

`runs/run_model6_comparison.py` 对比模型5原距离目标、模型6固定分批成本优化与联合分批成本优化；支持隔离子进程并发、逐次原子保存结果、输入及源码快照和独立物理审计。`tools/data/build_model6_static_suite.py --all-multibatch` 适配当前186个B2/B3配置，保留原物理数据并明确货物释放与合成费用假设。`analysis/summarize_model6_full_suite.py` 重新审计，输出完整箱数/批次数表、按来源与规模汇总、所有缺失对照及种子差异。见 [全量运行汇总](../output/model6_comparison/20261004_static_v2_full/full_summary.md)。
## 模型6两窗截点对照（2026-10-05）

`experiments.runs.run_model6_cutoff_comparison` 比较固定截点与联合优化截点。批次起止、顾客预约、需求、物理网络、车队、清洗与成本系数一致，二级第二阶段仍可回收。两组M6允许相同的订单分批及池规模决策，唯一允许域区别是截点。

ALNS从同一已重新审计的历史可行时间表开始，记录初始化来源、原预算、原文件哈希及重算成本；两组得到相同的新增搜索预算。独立精确求解不调用ALNS或使用这个启发式起点。初始解不继承任何成本最优证明。输出保留所有失败和超时、冻结输入和源码、独立审计、实际两窗长度，并区分成本、箱数、配送批次及M5时间表的最小需要箱数。

    .venv/bin/python -m experiments.runs.run_model6_cutoff_comparison --manifest data/model6/cutoff_v1_20261005/manifest.json --initial-study output/model6_comparison/20261004_static_v2_full --exact-time 30 --time 10 --iterations 30000 --seeds 42 43 44 --workers 4 --output /path/to/new/comparison

- [2026-10-17 精确模型压缩与短预算验证](../output/inspections/exact_compaction_20261017/report.md)：同机比较上一版本与按规模选择的等价 MILP 表达，包含建模计时、冷启动可行解、下界和保留的退化试验。
