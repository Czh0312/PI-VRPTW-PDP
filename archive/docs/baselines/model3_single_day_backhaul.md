> 历史基线，仅用于追溯。当前研究以 `../static_single_multibatch_study_plan.md` 和新版 M3 为准。旧实验文件已收入 `archive/cleanup_20260929/before_cleanup.tar.gz`。

# M3 单日基础版：两级配送与当日空箱闭环

版本：`single_day_backhaul_v1`。实现日期：2026-09-28。

当前毕设主场景采用 M3 固定顾客分仓，在同一个已知订单批次内联合安排两级配送与延后回收。两个精确后端、ALNS、独立路线审计与命令行均已接入本规则。

## 业务规则

一天一批可以使用多辆大车。每辆大车执行一条 `总仓 → 配送段 → 回收段 → 总仓` 路线，完成自己本趟全部配送后才取空箱，回收顺序由求解器决定，不强制按配送逆序行驶。

每个有需求的前置仓由一级车辆配送一次、回收一次。大车取箱前，须等这个仓的所有实际使用二级车辆回仓并完成卸箱；无需等其他仓同时完成。提前到达的大车可以等待。

二级仍可在路线中交替执行不同顾客的配送和回收。同一顾客的回收必须在其配送服务完成后，且满足回收时间窗；配送和回收允许由不同二级车完成。禁止空箱在顾客地点或额外中转站进行车辆间交接。

前置仓初始空箱库存为零，允许当天回收箱暂存。无损耗条件下，每个顾客的配送箱数等于回收箱数，全部当天送出的箱子须在日终期限内回到总仓，并完成配置要求的卸箱。容器池至少覆盖当天全部配送箱数；当天不清洗再投放，不发生跨批次周转。

沿用计划中的实施边界：

- 固定分仓；每辆一级车、二级车一天最多一趟。
- 每仓配送量和回收量均不可拆分；配送车与回收车不必相同。
- 允许纯配送车、纯回收车，仍计入原车队上限，报告分别统计。
- 仓内暂存空间与装卸资源视为充足，不模拟月台排队。
- 目标为两级总距离最小。等待与工时作为结果指标，尚未纳入费用目标。

## 运行方法

在项目根目录运行，Python 环境需具备项目依赖。精确法需要有效 Gurobi 许可证；ALNS 不依赖 Gurobi。

当前机器的 `.venv` 已补齐项目依赖，可直接使用 `.venv/bin/python` 代替下列 `python`，或先执行 `source .venv/bin/activate`。

```bash
# 单日主案例，ALNS
python main.py route --case config/cases/zhou_single_day.json --solver alns --time 10

# 默认货物流精确后端
python main.py route --case config/cases/zhou_single_day.json --solver exact --exact-time 60

# 用车辆索引后端交叉验证
python main.py route --case config/cases/zhou_single_day.json --solver exact --exact-backend indexed --exact-time 60

# 显式改为第 780 分钟前完成回总仓与卸箱
python main.py route --case config/cases/zhou_single_day.json --day-end 780
```

`python main.py` 的历史默认入口保持不变。新主线必须指定 `zhou_single_day.json` 或其他包含本运营策略的 M3 案例。将新策略用于其他模型会明确报错。

单次结果写到以下独立目录，每次带时间戳，不覆盖历史结果：

```text
output/single_runs/single_day_backhaul_v1/model3_<solver>/<数据名>/<时间戳>_seed<seed>/
  result.json       完整路线、策略、配置、求解状态、独立审计与最早可行时间表
  routes/           求解日志；精确法另含证书
  figures/          路线图
```

## 参数与时间含义

案例清单位于 [zhou_single_day.json](../../../docs/config/cases/zhou_single_day.json)，运营参数位于 [single_day_policy.json](../../../docs/config/routing/validation/single_day_policy.json)。案例也可以直接内嵌运营参数对象。

| 配置字段 | 示例值 | 含义 |
| --- | ---: | --- |
| `policy_version` | `single_day_backhaul_v1` | 选择整套单日业务规则 |
| `time_unit` | `minutes` | 时间窗、行驶、服务和日终均以分钟表示 |
| `dispatch_time` | 0 | 一级统一发车基准，已在此之前装车完毕 |
| `day_end` | 1440 | 当天最终回总仓并完成卸箱的期限，必须显式提供 |
| `e2_dispatch_prep_time` | 0 | 一级在前置仓卸货后，额外的二级准备时间 |
| `e2_return_unload_time` | 5 | 二级回仓后额外的卸箱时间 |
| `depot_return_unload_time` | 10 | 一级回总仓后额外的卸箱时间 |
| `container_pool_size` | `null` | 自动取当天配送箱数；可填更大的固定整数 |

这些示例的日终与装卸时间均标记为情景假设，不是实测营业时间。节点原有 `service_time` 仍用于顾客和前置仓服务；新增参数表示未包含在节点服务中的作业，调整数据时须避免重复计时。

前向关系为 `二级出发 ≥ 一级在该仓配送服务完成 + 二级准备时间`。回程关系为 `一级取箱开始 ≥ 该仓所有使用二级车的回仓卸箱完成时间`。最后一个取箱任务的服务完成时间不等于总仓日终完成时间，最终行驶与总仓卸箱必须另计。

输出策略中的 `e1_service_order`、`satellite_pickup_policy`、`allow_transfers` 和 `objective_mode` 是该版本的固定说明，不能在输入中分别改写。若需要其他规则，应作为后续版本实施。

## 审计与数量输出

`result.json` 中的 `route_audit` 完全根据提取后的路线重算，不读取求解器内部的时间变量：

- `valid` / `errors`：检查任务覆盖、车辆数、载重和容积、一级先送后取、顾客先送后取、两级时间衔接、日终与距离一致性。
- `timeline.e1` / `timeline.e2`：每站物理到达、服务开始/结束、等待，及每条路线的返回到达、卸箱完成和工作时长。
- `satellite_inventory`：各仓初始空箱零、收齐时间、一级取箱时间/数量、日内峰值暂存与日终库存。
- `container_balance`：顾客配送箱数、顾客收回箱数、一级带回总仓箱数。
- `time_metrics`：两级等待、两级总工作时长及系统最终完成时间。

对有效的“收齐后一次取走”路线，每仓峰值暂存恰为该仓当天箱数，日终空箱库存为零。这是当前无限库容假设对应的空间需求。数据不平衡、固定池不足或外部传入的任务与实例需求不一致，会在求解前报错。

统一报告从 `dispatch_time` 开始的最早可行时刻表。同样距离的路线可能有不同等待时间，因此不能把本版本称为等待最优。ALNS 无论是否恰好达到已知最优距离，都不自行声称完成最优性证明。

## 批量运行与可复现输出

```bash
# 同一小案例：两个精确后端 + ALNS 三个种子
python experiments/runs/run_single_day_backhaul.py --time 10 --exact-time 60

# 100 顾客代表实例
python experiments/runs/run_single_day_backhaul.py \
  --data data/zhou_later_pickup/large/standard_same_day/Set2_100-5-1b.csv \
  --fleet-config config/routing/validation/zhou_large.json \
  --solver alns --seeds 42 43 44 --time 30

# 小规模 standard_same_day 全集；通配符保留引号
python experiments/runs/run_single_day_backhaul.py \
  --data 'data/zhou_later_pickup/small/standard_same_day/*.csv' \
  --time 10 --exact-time 60
```

默认输出到 `output/experiments/single_day_backhaul/<时间戳>/`。可用 `--output` 指定新建或空目录；拒绝覆盖非空目录。`runs.csv` 和 `report.md` 同时记录成功与失败，JSON 保留全部路线和审计，manifest 保存配置、Python/依赖版本、输入/源文件哈希，`source_snapshot/` 保存运行时代码。精确法每个后端运行一次，ALNS 按列出的种子运行；`--seeds` 不修改 Gurobi 的随机参数。

配置包含道路矩阵时，CLI 与批量入口均使用给定距离和时间矩阵。更换 `--data` 时矩阵仍沿用所选案例，须与新节点集合匹配。Zhou 欧氏坐标的距离不直接解释为真实公里。

ALNS 的预算控制搜索；另行记录含构造和审计的墙钟耗时。精确法限时未证明最优的可行解与已证明最优的解分别标记。ALNS 未找到解只说明本次未找到可行解，不能据此宣称数学不可行。

## 验证与当前范围

业务回归覆盖局部收齐、跨仓先送后取、最后回程越界、非零装卸、二级混合送取、跨车顾客先后关系、零时长循环、守恒与策略隔离。测试入口：

```bash
python -m pytest -q tests/routing/test_single_day_backhaul.py
python -m pytest -q
```

完整回归 200 项通过（包含 26 项单日业务测试）。分别验证了 Python 3.12 和本机 `.venv` 的 Python 3.9.6，精确法使用 Gurobi 12.0.3；已有 ALNS 引擎的类型注解已补充延后求值以兼容 Python 3.9。

小规模代表例 `Set1_7-2-1b` 的两个精确后端均证明最优距离为 `337.61796790207916`，ALNS 三个种子得到相同距离并通过审计；7 箱全部返回总仓，最早完成时间为第 `735.266865` 分钟。见 [小规模验证报告](../../../docs/output/experiments/single_day_backhaul/final_validation_20260928/report.md)。

100 顾客/5 仓代表例的 ALNS 找到通过审计的可行解，距离为 `1890.954209`、最终完成时间为第 `1109.568087` 分钟，见 [大规模运行检查](../../../docs/output/experiments/single_day_backhaul/large_smoke_20260928/report.md)。这些是实现验收结果；22 个小实例与 18 个大实例的完整论文实验尚未完成。

现有 LaDe-D 代表日数据包含配送/回收箱数不等的设定，例如 `C29770` 为配送 20 箱、回收 17 箱，不满足本版无损耗闭环。接入前需另建有明确假设与来源的单日需求数据，不能直接将旧多日回收比例解释为当日全部回收。

`main.py cost` 暂不接受单日策略案例，因为现有经济入口会运行多个历史模型，且容器池与费用口径仍需调整。下一步依次是正式实验、单日经济评价、自由分仓，以及多批次/跨批暂存/有限库容扩展。这里没有改变历史 M1–M8 的默认业务语义。
