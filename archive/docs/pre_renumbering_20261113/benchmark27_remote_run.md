# 正式数据在其他电脑上的运行方法

仓库：`git@gitlab.com:pi-vrptw/PI-VRPTW_PDP.git`，分支：`main`。当前物理数据为 `data/formal_benchmark27_spacetime_v4_1/`：小、中、大各9例，总计27个基础算例；每个模型读取相同27例，共216份模型配置。两、三、四个外部需求时段各9例，卫星站数1—5个。设计与27例总表见[新版说明](../data/formal_benchmark27_spacetime_v4_1/README.md)。旧one_box_v1与multi-box版本及其冻结结果保持不变。

当前为物理数据修订4：小规模9例均为7位顾客，一级2辆、容量10/载重200，二级共4辆、每辆3箱/60。中规模仍为20/30位，大规模仍为50/100位，保留修订2的输入和运力。旧输入快照保留，使用新版应prepare到新目录。小规模子集的来源配对见新版selection.json。

小规模从v3同编号10顾客算例按数值顾客编号取前7位，保留订单时间窗、分仓、全部仓点和车队；部分仓—时段单元可为空，每个外部时段总需求仍非空。冻结哈希见[修订4登记](../config/experiments/benchmark27_spacetime_v4_1/frozen.json)。

本轮仅更新物理算例，不设计费用，不报告经济优劣。每份economic.json保留旧费用字节，供运行兼容；开展正式成本比较时应另行冻结选定的费用参数。M1—M4原生优化距离，M5—M8使用现有成本目标。生成/可行性见证与正式ALNS性能实验分开记录。

新算例必须重新prepare到独立输出目录。历史driver.py与历史结果继续保留其原输入，不覆盖或混用。全部运行输入在新版数据集内部，可行性见证不自动作求解器初解。

## 拉取和安装

首次获取：

```sh
git clone git@gitlab.com:pi-vrptw/PI-VRPTW_PDP.git
cd PI-VRPTW_PDP
```

已有仓库，在自己的工作目录内执行：

```sh
git switch main
git pull --ff-only origin main
git rev-parse HEAD
```

安装使用项目的可编辑模式。下面为 macOS/Linux 命令；Windows 将 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`。Python 至少3.9，本次核验使用3.9.6；用于同一性能对比的电脑应统一 Python 和依赖版本。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]' -c config/experiments/benchmark27_v1/validated_constraints.txt
```

约束文件保存本次验证使用的直接依赖版本，并非完整依赖锁。精确求解还需本机具备覆盖实验规模的 Gurobi 许可证；ALNS 运行不需要启动 Gurobi。

## 先核验数据，再运行

```sh
.venv/bin/python -m tools.audit.audit_formal_benchmark27
.venv/bin/python -m tools.audit.certify_formal_benchmark27 --verify
```

应得到27个基础算例、216份模型配置通过输入检查，以及216份可行性见证重放通过。空间/时段专用审计命令为 `.venv/bin/python -m tools.audit.audit_benchmark27_spacetime`，会检查原坐标、货重、仓时段均衡与同规模总运力。准备实验时自动记录清单和全部冻结输入的SHA-256。

下面是两条运行入口示例，预算仅用于检查环境，不能直接作为已确定的论文实验方案。每次使用新的输出目录：

```sh
.venv/bin/python main.py route --case data/formal_benchmark27_spacetime_v4_1/model_1/small/S01/case.json --solver alns --time 30 --iterations 1000000000 --seed 42 --output output/experiments/benchmark27_spacetime_smoke/M1_S01_s42
.venv/bin/python main.py route --case data/formal_benchmark27_spacetime_v4_1/model_8/small/S01/case.json --solver alns --time 30 --iterations 1000000000 --seed 42 --output output/experiments/benchmark27_spacetime_smoke/M8_S01_s42
```

精确求解使用 `--solver exact --exact-time 300`。全部案例路径见 `data/formal_benchmark27_spacetime_v4_1/case_index.csv`。各例的 `parameters.json` 标明固定项和优化项；同编号配对时不要额外更改需求、费用或K候选域。

## 正式论文结果的记录口径

运行前冻结提交号、完整命令、种子列表、时间与迭代预算、求解器线程数、机器CPU/内存和并发数；保存 `python --version` 与 `python -m pip freeze`。单次公开入口会保存结果、输入、源码快照、日志与审计信息。不同机器的运行时间不能直接混作算法速度比较；分配任务时避免让某个模型只在某一台机器上运行。

正式结果单独保存在新的 `output/experiments/` 目录，保留全部首次尝试及失败状态。修订2正式结果在 `output/experiments/benchmark27_20261015_formal_v1/`，使用旧费用；`output/inspections/` 保存数据检查和短时试跑记录。历史多箱费用修订和当前顾客数量修订分别记录；顾客集合变化的新版算例需要重新求解，旧结果不能直接改标签复用。`tools.audit.review_formal_benchmark27` 是诊断驱动。

M1–M4原生目标是距离，M5–M8原生目标是成本，不能直接把两种目标值相减计算改进率。统一评价、同源分组及模型语义边界见 [正式对比协议](../data/formal_benchmark27_spacetime_v4_1/comparison_protocol.json) 和 [空间时段设计说明](../data/formal_benchmark27_spacetime_v4_1/README.md)。

## 全量冻结实验入口

`experiments.runs.run_formal_benchmark27` 复用已有的隔离进程、互斥锁、逐次哈希与不可变attempt机制。默认覆盖全部216份配置：精确每例300秒、种子42；ALNS默认仅种子42，每例30秒、迭代上限10^9；216次精确和216次ALNS，共432次调用，调用预算和19.8小时。并发2，精确及数值库均单线程。准备阶段重新核验全部输入和216份见证，并保存源码ZIP、输入副本、环境、依赖版本和完整命令。见证不作为求解初始解。

先用一个基础算例对八模型做短测，记录实测外层开销，再将正式批跑冻结到新的目录：

```sh
.venv/bin/python -m experiments.runs.run_formal_benchmark27 prepare --output output/experiments/benchmark27_spacetime_smoke --study-kind smoke --bases S01 --exact-seconds 3 --alns-seconds 3 --seeds 42 --overhead-basis "Initial environment smoke"
.venv/bin/python output/experiments/benchmark27_spacetime_smoke/driver.py run --output output/experiments/benchmark27_spacetime_smoke
.venv/bin/python -m experiments.runs.run_formal_benchmark27 prepare --output output/experiments/benchmark27_spacetime_formal --overhead-basis "Measured process overhead from the completed smoke study"
.venv/bin/python output/experiments/benchmark27_spacetime_formal/driver.py run --output output/experiments/benchmark27_spacetime_formal
.venv/bin/python output/experiments/benchmark27_spacetime_formal/driver.py pack --output output/experiments/benchmark27_spacetime_formal
```

重复 `run` 先验证源码和输入哈希，再跳过已有完整记录；`--retry-failed` 将执行错误另存为新attempt，首次失败仍保留。`STOP` 文件在当前活动调用结束后停止队列。`status` 只读取进度；`analyze` 验证完整记录并重建报告。发布前检查 `stage_gate.json`：执行/审计异常、与可行性见证相矛盾的不可行证明、与已证最优参照相矛盾的ALNS解均需要复核。完成不要求所有调用可行或最优。

`report.md` 与 `tables/` 分开报告规模、原生目标、共同循环费用、种子波动、来源块及精确参照误差。`source_current.zip` 与 `raw_artifacts.zip` 保存未改字节的源码和输入/全部尝试；`packed_artifacts.json`登记逐文件与压缩包哈希。原始副本留在本机但不重复提交。恢复时先验证压缩包哈希，再解压至实验目录；冻结配置保留原执行路径，异机重跑应从当前正式数据集重新prepare到新目录，而不改写历史冻结输入。
