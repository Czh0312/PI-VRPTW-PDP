# M5 精确算法与 ALNS 全规模补测（2026-10-04）

覆盖 `static_batch_suite_v2` Zhou 主对比集的 140 个多批次配置。
7/10/12 顾客为小规模、20/30 为中规模、50/100 为大规模，每种顾客数 10 个家族，各有 B2/B3 配置。

- 完整精确 MILP：140 次，每例 300 秒，种子 42。
- 独立 ALNS：420 次，每例三个种子 42/43/44，每种子 30 秒，迭代上限 1,000,000。
- 总计 560 次，求解预算上限合计 54,600 秒；4 个子进程并发，精确求解器每次 1 线程。
- 全量输出：`output/experiments/m5_comparison_20261004/full/`。
- 先等待 M1–M4 正式对比的 `progress.json` 标为 `COMPLETED`，再自动开始全量 M5。
- 使用当前共享箱池配置 `demand_ratio=1.0`，实际解析值写入冻结案例及逐次结果。
- 保持订单、固定分仓、车队、批次、清洗与周期规则，不增加箱池或放宽业务条件。
- 两种算法均从头独立运行；不调用 hybrid、不拼接历史上下界、不把 ALNS 的结果作为精确初解。

本次补测不修改求解算法。源码、输入、生成配置及 SHA256 在第一项正式求解前全部冻结。
失败、时限内无解、超时及错误原样保留，不自动重试。`preflight_small/` 和 `preflight_large/`
是短预算入口验证，不计入正式 560 次统计。

`progress.json` 是实时状态，`report.md`、`runs.csv`、`comparison.csv` 与 `summary.csv` 随完成项刷新。
只有 `COMPLETED` 才表示全部登记任务结束；最终生成 `verification.json`。
汇总按规模和批次数分别报告距离、耗时、可行数量、已证最优数量与 GAP。
可行解必须经过独立审计和连续两周期重放；只有已证最优参照才计算实际最优误差。
相对时限内精确解的差距允许为负；认证界差为 `(ALNS距离−有效下界)/ALNS距离`。
预算不同，不作同预算算法速度优劣结论。三种子与同源情景不是独立几何样本。

Windows 运行环境使用既有 Anaconda Python 3.11 与 Gurobi 11.0.3：

```powershell
$env:PYTHONPATH='C:/gurobi/win64/python311/lib'
$env:PYTHONUTF8='1'
$env:PYTHONHASHSEED='0'
$env:OMP_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
python experiments/runs/run_model5_benchmark.py --prepare-only --after-progress output/experiments/m1_m4_comparison_20261004/full/progress.json --output output/experiments/m5_comparison_20261004/full
python experiments/runs/run_model5_benchmark.py --resume --workers 4 --output output/experiments/m5_comparison_20261004/full
```

若中断，确认旧驱动与其求解子进程都已停止，再执行第二条恢复命令。
恢复校验全部冻结文件，仅跳过已经保存逐次记录的任务；预算、输入、种子和前置等待条件沿用登记。
不能同时启动多个恢复进程。

## 已完成的预检

- `preflight_small/`：7 顾客 B2/B3，精确各 5 秒、ALNS 三种子各 1 秒，共 8 次；两个精确解均已证最优，8 次全部通过审计和两周期重放。
- `preflight_large/`：100 顾客 B2/B3，精确各 5 秒、ALNS 种子 42 各 2 秒，共 4 次；4 次全部通过审计和两周期重放。
  两个精确运行均为 `TIME_LIMIT_BUILD`，保留已审计初解，不能据此宣称精确优化完成或已证最优。
- 汇总专项测试 3 项通过，验证 B2/B3 分组、失败种子计数、未知最优误差留空、下界冲突不进入规模均值及生成配置哈希保护。
- 后台驱动信息保存于 `output/experiments/m5_comparison_20261004/launcher.json`。
  正式驱动及子进程均使用冻结源码，等待前一轮实验完成后自动开始；当前状态以 `full/progress.json` 为准。

## Git 存档中的源码去重

仓库保存每个实验阶段的一份完整 `source_snapshot/`，逐次运行仍保留原始 manifest、结果、日志与输入。
重复的逐次源码副本仅在本地保留。可先校验全部逐次源码哈希，再按需恢复副本：

```powershell
python tools/data/restore_benchmark_sources.py --study output/experiments/m5_comparison_20261004
python tools/data/restore_benchmark_sources.py --study output/experiments/m5_comparison_20261004 --restore
```

恢复只复制该阶段冻结源码中与逐次 manifest 的 SHA256 完全匹配的文件，拒绝覆盖已修改副本。
