# 正式数据在其他电脑上的运行方法

仓库：`git@gitlab.com:pi-vrptw/PI-VRPTW_PDP.git`，分支：`main`。主数据为 `data/formal_benchmark27_v1/` 修订2，八模型各有小、中、大规模，每档九例。同编号跨模型配对，正式输入无需下载旧原始数据或解压历史结果。

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

应得到216份配置通过输入检查、1,092个文件哈希通过，以及216份可行性见证重放通过。正式清单的 SHA-256 是 `6a33b3c9f5766221af321841bfb66f2eb5a1cce963884055542151c072539e3a`。

下面是两条运行入口示例，预算仅用于检查环境，不能直接作为已确定的论文实验方案。每次使用新的输出目录：

```sh
.venv/bin/python main.py route --case data/formal_benchmark27_v1/model_1/small/S01/case.json --solver alns --time 30 --iterations 1000000000 --seed 42 --output output/experiments/benchmark27_smoke/M1_S01_s42
.venv/bin/python main.py route --case data/formal_benchmark27_v1/model_8/small/S01/case.json --solver alns --time 30 --iterations 1000000000 --seed 42 --output output/experiments/benchmark27_smoke/M8_S01_s42
```

精确求解使用 `--solver exact --exact-time 300`。全部案例路径见 `data/formal_benchmark27_v1/case_index.csv`。各例的 `parameters.json` 标明固定项和优化项；同编号配对时不要额外更改需求、费用或K候选域。

## 正式论文结果的记录口径

运行前冻结提交号、完整命令、种子列表、时间与迭代预算、求解器线程数、机器CPU/内存和并发数；保存 `python --version` 与 `python -m pip freeze`。单次公开入口会保存结果、输入、源码快照、日志与审计信息。不同机器的运行时间不能直接混作算法速度比较；分配任务时避免让某个模型只在某一台机器上运行。

正式结果单独保存在新的 `output/experiments/` 目录，保留全部首次尝试及失败状态。当前 `output/inspections/` 是数据检查和短时试跑记录；正式多种子性能对比尚未完成。`tools.audit.review_formal_benchmark27` 是诊断驱动，不代表已经冻结的论文主实验方案。

M1–M5原生目标是距离，M6–M8原生目标是成本，不能直接把两种目标值相减计算改进率。统一评价、同源分组及模型语义边界见 [正式对比协议](../data/formal_benchmark27_v1/comparison_protocol.json) 和 [设计说明](benchmark27_design_20261015.md)。
