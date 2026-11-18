# 27 例六模型共享基准 v2

本目录用于复现 v2 运行。每个基础算例由一个 `case.json` 引用同目录的未分仓订单、路网和车队。算例编号与来源沿用 v1。[当前每例一表的数据](../../../data/README.md) · [v2 算例索引](case_index.csv) · [共同费用配置](economic.json)

| 模型 | 分仓规则 | 调度规则 |
| --- | --- | --- |
| 1 | 按最近前置仓固定 | 一次性包装，单批配送 |
| 2 | 联合优化顾客分仓与路线 | 一次性包装，单批配送 |
| 3 | 按最近前置仓固定 | 循环箱，单批集中交接 |
| 4 | 联合优化顾客分仓与路线 | 循环箱，单批集中交接 |
| 5 | 按最近前置仓固定 | 优化批次数、批次分界、回收截点和箱池 |
| 6 | 按最近前置仓固定 | 无批次，分次回收、清洗和领用 |

最近仓按前置仓到顾客的网络距离选取。本基准使用坐标的欧氏距离，并列时取按字符串排序最靠前的仓 ID。模型 2、4 将最近仓分配用作启发式初始分仓。同一顾客的订单共用一个仓。单批和可变批次的日历在加载时由模型选择，模型 3–6 默认在次日首单释放前完成复位。`compare-single-batch` 为四个模型使用同一份固定单批日历。

与 v1 的分仓相比，按最近仓规则有 21 例共 86 单换仓。`provenance.json` 中的仓区统计记载 v1 的设计来源。复现 v1 方案及其费用时使用对应的 [v1 算例](../benchmark27_models_1_6_v1/README.md)。

```sh
python main.py route --case data/benchmark27_shared_v2/small/S03/case.json --model 3 --solver alns --time 2
python main.py route --case data/benchmark27_shared_v2/small/S03/case.json --model 4 --solver alns --time 2
python -m tools.audit.audit_current_benchmark --dataset archive/datasets/benchmark27_shared_v2
```

[清单](manifest.json)记录基础输入、费用配置及源版本哈希。审计加载 27 例的全部 162 个模型组合。`python -m tools.data.build_shared_benchmark --output <新目录>` 可从 v1 生成同结构的基准。
