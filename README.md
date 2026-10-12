# 2E-VRPTW-PDD

静态全信息下，两级配送与容器循环的固定分批及分批日历优化。
正式执行规则为 `multi_batch_cutoff_v1`，B=1 是单批次，B>1 是多批次；周期长度由输入日程决定。

[文档导航](docs/README.md) · [开发指南](docs/development.md) · [模型布局](docs/model_layout.md) · [数据说明](data/README.md) · [实验入口](experiments/README.md) · [结果索引](output/README.md)

## 模型

| 编号 | 研究角色 | 求解方式 |
| --- | --- | --- |
| M1 | 一次性包装、固定分仓、纯配送，B=1 | MILP / ALNS |
| M2 | 一次性包装、自由分仓、纯配送，B=1 | 联合分仓 MILP 或分仓域树 / ALNS |
| M3 | 单批次循环包装、固定分仓，B=1 | MILP / ALNS |
| M4 | 单批次循环包装、自由分仓，B=1 | 联合分仓 MILP 或分仓域树 / ALNS |
| M5 | 多批次循环包装、固定分仓，B>1 | MILP / ALNS |
| M6 | 分批、箱池与路线成本优化：等长批次、共同分界 | MILP / ALNS |
| M7 | 分批、箱池与路线成本优化：等长批次、逐批独立分界 | MILP / ALNS |
| M8 | 分批、箱池与路线成本优化：不等长批次、逐批独立分界 | MILP / ALNS |

M1/M3、M2/M4 分别用于固定/自由分仓的包装对照；M1/M2、M3/M4 用于分仓价值对照。
循环模型包含回收、清洗、有限箱池与周期闭合。M6–M8 的允许批次数由周期和最短阶段约束确定；显式 K 子集只覆盖相应诊断范围，见[日历设计](docs/model7_model8_calendar_design.md)和[批次数域说明](docs/cost_and_batch_domain_20261007.md)。

已保存[单批 M1–M4 对比](output/experiments/m1_m4_comparison_20261004/m1_m4_solver_comparison_20261004.md)、[M5 全规模补测](output/experiments/m5_comparison_20261004/m5_solver_comparison_20261004.md)及[M3/M5/M6/M7/M8 成本对比](output/model35678_comparison/README.md)。各报告分别记录预算、种子、失败和证明范围；当前尚缺完整真实闭环经营数据。
历史 M1–M4 仅通过 `--baseline` 调用；已归档的旧 M5–M8 与当前编号定义不同。

## 快速运行

在仓库根目录执行。新环境的安装步骤见[开发指南](docs/development.md)。

```sh
# 默认单批机制例（M3，ALNS）
.venv/bin/python main.py route --time 2

# 多批机制例（M5，ALNS）
.venv/bin/python main.py route --case config/experiments/examples/multibatch_demo.json --time 2

# M8 日历优化示例；使用案例中声明的费用与允许域
.venv/bin/python main.py route --case config/experiments/examples/model8_demo.json --time 2

# 单批四模型与条件经济分析（示例费用）
.venv/bin/python main.py compare-single-batch --time 1 --exact-time 10

# 只列出数据，不启动求解
.venv/bin/python main.py compare --dataset-manifest data/manifest.json --batch-counts 1 --list-only

# 已审计方案的费用核算
.venv/bin/python main.py cost --route-result /path/to/result.json --economic-config config/cost/synthetic/multibatch_synthetic.json

# 日常基础检查（完整回归用 make test-full）
make test
```

精确求解需要 Gurobi 和有效许可证；ALNS 求解入口不加载 Gurobi。
单次输出默认写入 `output/single_runs/`，可用 `--output` 指向新的空目录。

## 目录职责

| 目录 / 文件 | 内容 |
| --- | --- |
| `main.py`、`cli/` | 命令分发、单次执行与结果输出 |
| `pyproject.toml`、`Makefile` | 依赖、测试配置与日常检查命令 |
| `models/routing/model_1/`～`model_8/` | 每个模型独立拥有 `alns/`、`exact/`，见[逐文件说明](models/routing/README.md) |
| `models/common/` | 两种算法共用的数据、业务规则、结果与独立审计 |
| `archive/routing_baselines/` | 显式调用的历史 M1–M4 及旧搜索组件 |
| `models/cost/` | 成本、生命周期与经济对照 |
| `config/` | 按车辆、容器、运行规则、成本、求解器、数据生成、实验组合分类的参数 |
| `tools/configuration/` | 配置读取和路径解析的 Python 代码 |
| `data/` | 数据集、配对清单、原始来源和派生记录 |
| `experiments/` | 批量运行与结果分析的程序；生成文件写入 `output/` |
| `tools/` | 数据构造、审计、恢复与公用绘图（`tools/reporting/`） |
| `tests/` | 路由、费用和数据回归测试 |
| `docs/` | 当前计划及必要的模型、数据和使用说明 |
| `project/` | 聊天总结 skill 生成的交接记录 |
| `output/` | 唯一成果目录：实验结果、图片、报告、Excel 及复现材料 |
| `reference/`、`archive/` | 参考材料与历史归档 |

## 数据与研究记录

现行基础数据为 `static_batch_suite_v2`。`data/single_batch/` 与 `data/multi_batch/` 按 `small/medium/large` 管理，共同网络和资源在 `data/shared/`；`data/manifest.json` 索引 93 组 B1/B2/B3 配对。M6–M8 的适配数据、当前配置及来源见[数据说明](data/README.md)与[配置说明](config/README.md)。

费用包含合成情景和有来源说明的参考参数，各自适用范围见[费用复核](docs/cost_calibration_review_20261006.md)。真实配送基础不等于完整真实闭环数据，已保存方案重计价也不等于按新目标重新求解。

当前计划与说明见[文档导航](docs/README.md)，已结束的逐轮开发记录见[历史归档](archive/docs/README.md)。正式报告随对应实验保存在 `output/`；复现方法见[开发指南](docs/development.md)。
