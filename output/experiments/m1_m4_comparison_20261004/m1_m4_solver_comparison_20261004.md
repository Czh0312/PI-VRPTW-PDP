# M1–M4 精确与 ALNS 完整规模对比（2026-10-04）

本次使用拉取后的当前源码和 `static_batch_suite_v2`，执行 Zhou 主对比集全部 70 个 B=1 案例。
小型为 7/10/12 顾客各 10 例，中型为 20/30 顾客各 10 例，大型为 50/100 顾客各 10 例。
四模型目标均为总距离。固定分仓的包装对照为 M1/M3，自由分仓为 M2/M4。

## 正式运行登记

- 输出：`output/experiments/m1_m4_comparison_20261004/full/`。
- 精确：每个案例×模型 300 秒，种子 42，280 次。
- ALNS：每个案例×模型×种子 30 秒，种子 42/43/44，迭代上限 1,000,000，840 次。
- 共 1,120 次，4 个独立子进程并发。每个精确求解器 1 线程。
- 全部预算上限合计 109,200 秒；实际墙钟受提前收敛、建模、进程启动、审计和机器负载影响。
- 每项保留原始输出、独立审计、两周期重放、状态、距离、下界和证明信息。失败/超时不剔除、不重试。
- 冻结源码、全部输入、生成配置和 SHA256；后续工作区代码修改不影响本次求解。

`progress.json` 是当前执行状态，`report.md` 随运行更新。只有状态为 `COMPLETED` 才代表本轮全部执行完成。

```powershell
$env:PYTHONPATH='C:/gurobi/win64/python311/lib'
$env:PYTHONUTF8='1'
$env:PYTHONHASHSEED='0'
$env:OMP_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
python experiments/runs/run_single_batch_benchmark.py --exact-time 300 --time 30 --seeds 42 43 44 --workers 4 --output output/experiments/m1_m4_comparison_20261004/full
```

如果主进程中断，先确认原进程和它的求解子进程已经停止，再恢复：

```powershell
python experiments/runs/run_single_batch_benchmark.py --resume --workers 4 --output output/experiments/m1_m4_comparison_20261004/full
```

恢复会验证冻结源码与输入，仅跳过已经保存逐次记录的任务。不得同时运行两个恢复进程。
预算和种子沿用最初 `manifest.json`，不会用恢复命令的默认预算覆盖。

## 结果字段的含义

`runs.csv` 为每次实际运行；`comparison.csv` 为每个案例×模型的四次运行配对；
`summary.csv` 按小/中/大规模和模型汇总；`verification.json` 在最终完成时生成。

- ALNS 最好、均值、最差和总体标准差，同时报告可行种子数和实际已完成数。
- 相对精确解差距为 `(ALNS距离−精确距离)/精确距离`，可为负；超时精确解只是可行参照。
- 只有精确已证最优时，才填“实际最优误差”。
- 有效下界对应的认证界差为 `(ALNS距离−下界)/ALNS距离`，是误差上界，不是已知实际最优误差。
- “时限内无解”不代表“已证不可行”；分仓子问题最优不代表自由分仓全局最优。
- 规模平均差距仅对双方有可行解、该组全部运行已完成的案例计算，配对数量明确列出。
- 精确与 ALNS 预算不同，本次可以比较解质量和实际耗时，不能宣称同预算算法优劣。
- 三种子和同源两个情景不能作为独立几何样本。距离单位沿用来源，不将 Zhou 坐标距离称为公里。

## 当前 Windows 环境修复与预检

默认 Anaconda Python 3.11.3 没有安装 gurobipy，使用已有 Gurobi 11.0.3 的
`C:/gurobi/win64/python311/lib`，没有新安装依赖。沙箱账户与本机许可证用户名不同，
正式精确求解运行于已验证许可证的 Windows 用户环境。

数据清单有 1,167 个文件因 Git 的 `core.autocrlf=true` 转成 CRLF 而产生字节哈希差异。
逐文件验证替换为 LF 后的 SHA256 与原清单完全一致才恢复字节；无业务数据差异。
恢复后全部 70 个主对比案例通过清单校验和配对检查。`.gitattributes` 新增 `/data/** -text`，
避免 Git 再次改变清单按字节登记的数据。数据与 HEAD 原始内容一致。

保留预检结果：

- `preflight_small/`：7 顾客，精确 3 秒、ALNS 1 秒。M1/M2/M3 已证最优；M4 精确有可行解但未证最优。8 次均可行。
- `preflight_large/`：100 顾客，精确 5 秒、ALNS 2 秒。四模型 ALNS 均可行；精确 M1/M2 有可行解，M3/M4 时限内无解。不是正式性能评测。
- `runner_validation/`：人工机制例，精确 3 秒、ALNS 三种子各 1 秒，16 次均通过审计及周期重放，四个精确解全部已证最优。
- 对比计算额外验证了 ALNS 优于时限精确解、未知最优时不填实际最优误差、认证界差分母、下界矛盾和最优声明矛盾检测。

预检采用独立目录，不混入正式 1,120 次统计。

## 四模型横向对比表

按用户补充要求，新增 `analysis/summarize_single_batch_models.py`，只读取本轮已保存的结果，
不改变运行中的冻结源码、模型、参数或求解预算。

- `full/model_comparison.md`：四模型并列，按规模、顾客数和逐例查看。
- `full/model_comparison_cases.csv`：每例×求解口径的 M1–M4 距离、M2/M1 和 M4/M3 距离减少量及比例。
- `full/model_comparison_summary.csv`：各顾客数和各规模的共同完整案例汇总。
- `output/experiments/m1_m4_comparison_20261004/M1-M4模型对比.xlsx`：汇总与逐例结果，两张表。

距离减少率为 `(原模型距离−自由分仓模型距离)/原模型距离`。
正数表示减少、负数表示退步；百分比列使用逐例百分比的算术平均，不是直接拿两个平均距离相除。
CSV 另外保留按距离加权的比例以便检查，Excel/Markdown 主表明确使用逐例平均。

精确时限可行解、ALNS 三种子最好、ALNS 三种子均值分别列出。
ALNS 须三个登记种子均完成且通过审计才统计该模型；未完成或无解保留缺失状态。
汇总四个平均距离和两组减少率必须来自同一组四模型全部完整可行的案例，数量明确列出。
逐例表中，某一对模型都有结果即可计算该对减少率，即便另一对还未完成。
精确解未证最优时只表示本次可行结果的横向差距，不能当作模型最优目标的差距。

刷新进程每 30 秒检查已保存结果，Markdown/CSV 随新结果刷新，Excel 每 5 分钟刷新。
主实验完成时，再生成最终 Excel 并退出。进程与刷新状态保存在
`full/model_comparison_watcher.json` 和 `full/model_comparison_refresh.json`。
Excel 每次导出均独立复核 270 个汇总值、变化/缺失/零值输入重算、公式错误及保存结果，并渲染两张表。

Git 存档保留每阶段一份完整冻结源码，省略逐次运行的重复源码副本；逐次 manifest、结果、日志和输入仍保存。
使用 `python tools/data/restore_benchmark_sources.py --study output/experiments/m1_m4_comparison_20261004`
校验逐次源码哈希；追加 `--restore` 可按原始 SHA256 恢复所有副本，并拒绝覆盖已修改文件。
