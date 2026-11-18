# 两级配送与循环容器路径优化

| 模型 | 包装与分仓 | 调度规则 | 求解 |
| --- | --- | --- | --- |
| 1 | 一次性包装，固定分仓 | 单批纯配送 | MILP、ALNS |
| 2 | 一次性包装，自由分仓 | 单批纯配送 | MILP、ALNS |
| 3 | 循环箱，固定分仓 | 单批，各前置仓收齐后一次交接 | MILP、ALNS |
| 4 | 循环箱，自由分仓 | 单批，各前置仓收齐后一次交接 | MILP、ALNS |
| 5 | 循环箱，固定分仓 | 优化批次数、批次长度、截点、箱池及路线。原模型 8 | MILP、ALNS |
| 6 | 循环箱，固定分仓 | 无批次，两级多趟、分次回收、洗净后可当期复用。原模型 10 | HiGHS 或 Gurobi MILP、ALNS |

模型 3–6 默认由订单释放、顾客送取时间窗、行驶、交接及清洗顺序共同决定作业时间，箱子和车辆须在次日首单释放前复位。模型 5 同时优化首批装车、批次分界和批内回收截点，并按实际周转时刻记录末批结束。使用固定营业窗口可加 `--fixed-work-window`。复现历史日历时选择对应的归档配置。[基准数据与归档](data/basetest/README.md)

当前实验使用 `data/basetest/` 下的模拟基准数据集，包含 27 个基础算例。小、中、大各 9 例，分别有 7、20、50 位顾客。默认演示使用 S01。每个算例的六个模型读取同一份未分仓订单、路网和车队。固定分仓模型在加载时按最近前置仓分配顾客，自由分仓模型在求解时选择前置仓。[算例清单](data/basetest/README.md) · [数据集目录](data/README.md)

## 运行

从仓库根目录使用已配置的项目解释器运行。

```sh
# 模型 3 的 S01，按每期总成本求解
python main.py route --model 3 --solver exact --exact-time 60
# 模型 4 的 S01，自由分仓和集中交接
python main.py route --model 4 --solver alns --time 2
# 新模型 5（原模型 8），可变批次
python main.py route --model 5 --solver alns --time 2
# 新模型 6（原模型 10），无批次
python main.py route --model 6 --solver alns --time 2
# 检查 basetest 的 27 张算例表及 162 个算例与模型组合
python -m tools.audit.audit_current_benchmark
```

用 `--case S03` 选择算例表，用 `--model` 选择模型。`--case` 也接受算例的 CSV 路径。六个模型使用共同的模拟费率最小化每期总成本。运输费为 \(0.1D_1+0.05D_2\)，M1、M2 加当期纸箱费用 \(5Q\)，M3–M6 加箱池日使用费 \(5N\) 和清洗等费用 \(0.01Q\)。其中 \(Q\) 为配送箱次，\(N\) 为当期需要配置的实体循环箱数。[每期费用](docs/model3_cost_comparison.md)

模型 1–4 的每期成本对照可用 `compare-single-batch`。当前基准使用共同的固定作业窗口，模型 3、4 按实际收齐时刻集中交接。[每期节省与损耗敏感性](docs/single_batch_comparison.md)

单次运行默认写入 `output/current/model_N/`。已冻结的旧实验按各自当时的输入、目标函数和模型编号解读。[结果索引](output/README.md) · [历史档案](output/history/pre_renumbering_20261113/README.md) · [文档导航](docs/README.md)
