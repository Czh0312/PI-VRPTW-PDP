# basetest 模拟基准数据集

每个算例一张 CSV 表。每行是一个总仓、前置仓或顾客。`node_type` 的 `d`、`s`、`c` 分别表示这三类点位。顾客行记录箱数、货重、释放时间、送取时间窗及用箱时长。时间以零点起算的分钟表示，服务时间与用箱时长也以分钟计。

| 规模 | 每例顾客数 | 算例 | 车队与算例配置 |
| --- | ---: | --- | --- |
| 小 | 7 | [S01](small/S01.csv)–S09 | [small/config.json](small/config.json) |
| 中 | 20 | [M01](medium/M01.csv)–M09 | [medium/config.json](medium/config.json) |
| 大 | 50 | [L01](large/L01.csv)–L09 | [large/config.json](large/config.json) |

同一规模的所有算例和六个模型共用该目录 `config.json` 的 `fleet`。可用车数、载重和容积采用该规模原有配置中的最高值。车辆实际启用数量由求解得出的路线和调度确定。固定分仓模型加载时按最近前置仓分配，自由分仓模型在求解时选择仓。[每期费用](../../docs/model3_cost_comparison.md)

三个规模共用数据集根目录的一份 `economic.json` 作为每期成本的默认费率。[模拟费率](economic.json)

M1、M2 在共同的 08:00–21:00 作业窗口内遵守顾客配送时间窗。M3、M4 在各前置仓收齐空箱后安排交接。M5 由求解器确定批次数、批次分界及截点。M3–M6 默认以次日首单释放作为复位边界。

```sh
python main.py route --case S03 --model 3 --solver alns --time 2
python main.py route --case S03 --model 4 --solver alns --time 2
python -m tools.audit.audit_current_benchmark
```

`--case` 也接受 `data/basetest/small/S03.csv` 这样的路径。旧实验使用对应版本的输入复现。[旧版共享基准 v2](../../archive/datasets/benchmark27_shared_v2/README.md) · [六模型基准 v1](../../archive/datasets/benchmark27_models_1_6_v1/README.md)
