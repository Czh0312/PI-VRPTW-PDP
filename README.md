# 两级配送与循环容器路径优化

| 模型 | 包装与分仓 | 调度规则 | 求解 |
| --- | --- | --- | --- |
| 1 | 一次性包装，固定分仓 | 单批纯配送 | MILP、ALNS |
| 2 | 一次性包装，自由分仓 | 单批纯配送 | MILP、ALNS |
| 3 | 循环箱，固定分仓 | 单批，各前置仓收齐后一次交接 | MILP、ALNS |
| 4 | 循环箱，自由分仓 | 单批，各前置仓收齐后一次交接 | MILP、ALNS |
| 5 | 循环箱，固定分仓 | 优化批次数、批次长度、截点、箱池及路线。原模型 8 | MILP、ALNS |
| 6 | 循环箱，固定分仓 | 无批次，两级多趟、分次回收、洗净后可当期复用。原模型 10 | HiGHS 或 Gurobi MILP、ALNS |

模型 3–6 默认由订单释放、顾客送取时间窗、行驶、交接及清洗顺序共同决定作业时间，箱子和车辆须在次日首单释放前复位。模型 5 同时优化首批装车、批次分界和批内回收截点，并按实际周转时刻记录末批结束。复现模型 5 的固定营业窗口可用 `--fixed-work-window`。

正式基准数据包含小、中、大各 9 例，分别有 7、20、50 位顾客。同一算例的六个模型共用路网和车辆。[数据与算例清单](data/README.md)

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
# 检查 162 个模型入口的文件哈希和输入
python -m tools.audit.audit_current_benchmark
```

指定其他算例时传入 `--case`。模型 3、5、6 的正式示例按每期总成本 \(0.1D_1+0.05D_2+5N+0.02Q\) 优化。模型 1–4 的生命周期经济对照可用 `compare-single-batch`，该入口使用共同的固定日历。[生命周期比较](docs/single_batch_comparison.md)

单次运行默认写入 `output/current/model_N/`。已冻结的旧实验按各自当时的输入、目标函数和模型编号解读。[结果索引](output/README.md) · [历史档案](output/history/pre_renumbering_20261113/README.md) · [文档导航](docs/README.md)
