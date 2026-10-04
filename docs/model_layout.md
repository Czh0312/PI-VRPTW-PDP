# 模型编号与代码位置

2026-09-29，按用户确认的设计整理。编号表示研究模型，各模型恢复独立文件夹。

| 编号 | 模型 | 批次 | 位置 |
| --- | --- | --- | --- |
| M1 | 固定分仓、纯两级配送 | 单批次对照 | `models/routing/variants/model_1/` |
| M2 | 自由选仓、纯两级配送 | 单批次对照 | `models/routing/variants/model_2/` |
| M3 | 固定分仓、容器配送与回收 | 单批次 B=1 | `models/routing/variants/model_3/` |
| M4 | 自由选仓、容器配送与回收 | 单批次 B=1 | `models/routing/variants/model_4/` |
| M5 | 固定分仓、有限容器跨批周转 | 多批次 B>1 | `models/routing/variants/model_5/` |

M1/M2 在经济对照中使用一次性包装，不承担容器逆向任务。M3/M4 负责单批次闭环。
M5 使用固定日程和订单批次，通过回收、回运、清洗支持跨批复用，并在大周期末恢复全池清洁状态。
当前 M5 仍是固定分仓；多批次自由选仓不是这次目录调整新增的功能。

```text
models/routing/
├── variants/
│   ├── model_1/
│   ├── model_2/
│   ├── model_3/
│   ├── model_4/
│   └── model_5/
├── common/
│   └── cycle/          输入、业务规则、结果格式与独立审计
├── solvers/
│   ├── exact/          MILP、解提取、自由选仓穷举与证明汇总
│   └── alns/           初解、候选评价、路线与自由选仓启发式
├── baselines/          显式调用的历史 M1–M4
├── catalog.py          模型编号和适用范围
└── service.py          按编号调用对应模型入口
```

五个模型目录均包含：

```text
model.py               模型定义、范围校验；保留旧建模 API 的按需转发
exact/
  __init__.py          精确求解 API
  model.py             本模型的 MILP 建模入口
  solver.py            精确求解入口
alns/
  __init__.py          ALNS API
  solver.py            ALNS 求解入口
__init__.py            公共 API
README.md              本模型说明
```

求解服务实际调用这些入口，各入口检查模型身份和批次数。M3 不能接收多批输入，M5 不能接收单批输入。
结果记录 `model`、`batch_mode`、`model_module`、`solver` 和 `solver_module`，可追溯模型与实际求解实现。

## 精确求解与 ALNS 的分离

| 内容 | 精确求解 | ALNS |
| --- | --- | --- |
| 底层目录 | `solvers/exact/` | `solvers/alns/` |
| 固定分仓实现 | `formulation.py`、`solver.py` | `construction.py`、`evaluator.py`、`solver.py` |
| M2/M4 自由选仓 | `exact/assignment.py`，联合 MILP 或完整枚举并汇总证明 | `alns/assignment.py`，启发式搜索分仓 |
| Gurobi 依赖 | 有，需有效许可证 | 无 |
| 最优性 | 由求解状态、界和分仓覆盖情况判断 | 不声明已证明最优 |

两套实现不互相导入。各模型的公共入口按需加载选定方法，调用 ALNS 时不加载精确求解代码或 Gurobi。
共同层 `common/cycle/` 不含求解算法；其中 `assignment.py` 只做分仓校验、实例转换与结果恢复。
输入、日程、容器规则和结果格式共用；两种方法输出的方案都接受相同的独立审计与周期重放。

M1/M3/M5 构建固定分仓 MILP；M2/M4 的精确算法枚举顾客分仓，其 `exact/model.py` 提供 `build_subproblem`，
单个子问题的最优性不能冒充自由选仓全局最优。此次拆分没有改变数学约束或搜索函数的计算逻辑。

## 数据与模型对应

`data/single_batch/{small,medium,large}/` 的同一案例供 M1–M4 对照；
`data/multi_batch/{small,medium,large}/` 的 B2/B3 案例由 M5 求解。
Zhou 主对比集按小型 7/10/12、中型 20/30、大型 50/100 顾客划分，每种顾客数 10 例（5 来源块×2 情景）；加上补充数据，单批小中大分别有 39/34/20 例，多批为 78/68/40 个配置；配对资源共用 `data/shared/`。
数据目录按批次与规模划分，模型目录按编号与求解方法划分。来源单独记录为 `source_kind`，不再把真实配送基础另列为第三种批次类别。
详见 [数据目录](../data/README.md)。

## 与之前目录的关系

原先 `model_3/multibatch/` 混放了共同机制和多批次实现，`variants/single_batch/` 又散放了 M2/M4 的搜索代码。
共同业务规则现在放在 `common/cycle/`，两套求解实现分别放在 `solvers/exact/` 与 `solvers/alns/`；原来的混合目录已移除。
按批次分组的 `single_batch/model_n`、`multi_batch/model_n` 方案没有采用。

新版 M5 是本研究的多批次模型；旧 M5 中转模型仍在清理归档中，旧 M6–M8 不在当前模型注册表。
单批数据默认用 M3，多批数据默认用 M5。2026-09-29 模型迁移时，原有 207 个配对配置中，69 个 B1 保留 M3，138 个 B2/B3 改为 M5；2026-09-30 按最新顾客数重新分组后，每档扩展到 10 例后，活动清单为 93 个 B1 与 186 个 B2/B3。
网络、订单、车队、日程和来源文件未修改；仅更新模型标签、生成器和配置清单哈希。

## 运行

```sh
# M1–M4 使用相同单批案例，可替换编号
.venv/bin/python main.py route --case config/cases/single_batch_demo.json --model 4 --time 2

# 当前多批次模型 M5
.venv/bin/python main.py route --case config/cases/multibatch_demo.json --model 5 --time 2

# 单批包装与选仓对照仍是 M1–M4
.venv/bin/python main.py compare-single-batch --time 1 --exact-time 10

# 单批/多批配对实验将分别使用 M3 和 M5
.venv/bin/python main.py compare --dataset-manifest data/manifest.json \
  --families mechanism_forced_late --batch-counts 1 3 --solver alns --time 1 --no-plots
```

## 旧结果与恢复

已有运行目录中的源码、输入和结果快照不改写，以保留原始研究证据。
以前将多批次暂标为 M3 的结果，需要显式迁移后才能作为当前 M5 的结果使用：

```sh
.venv/bin/python tools/audit/migrate_cycle_result.py \
  --case config/cases/multibatch_demo.json \
  --route-result /path/to/former_M3_multibatch_result.json \
  --output /path/to/new_M5_result.json
```

迁移只接受 schema 2、`multi_batch_cutoff_v1`、B>1 的原 M3 周期结果；输出新文件并重新独立审计，
保留原始文件路径与 SHA-256。不会接受旧中转 M5，不改路线、访问顺序或时间表，也不覆盖原文件。
原始快照依然可按各自保存的源码重现。

重排前源码备份位于 `archive/model_layout_20260929/before_layout.tar.gz`，已逐文件校验；
标签修改前的案例/清单备份为同目录的 `before_model5_labels.tar.gz`。该目录还记录模型编号迁移清单。
本次求解器拆分前的代码备份为 `archive/solver_separation_20260929/before_separation.tar.gz`，
对应清单记录文件哈希；`before_results.json` 保存拆分前的对照结果。

## 模型编号整理的验证

- 五个编号模型各运行一次精确求解和 ALNS，共 10 个有效方案；全部通过独立审计及两周期重放。
- 冻结副本的配对实验再运行 M3/B1 与 M5/B3，各精确和 ALNS，共 4 次，全部有效且模型标签正确。
- 原有 207 份时间表全部复核：69 份单批保持 M3，138 份多批在内存显式重标为 M5 后通过独立审计。原文件未修改。
- 全库测试为 **231 passed / 1 failed**。剩余失败是既有容器池文件使用 schema 1，而读取器要求 schema 2；
  与本轮模型迁移无关，保留用户配置数值，没有擅自转换成另一套池规模。
- 结果与审计索引见 [本轮验证记录](../output/experiments/model_layout_20260929/validation.json)。

## 求解器拆分的验证

- 新增 3 项独立进程测试：精确求解禁止加载 ALNS，ALNS 禁止加载精确求解和 Gurobi，共享层禁止加载任一求解器。
  前两项各实际求解五个模型，并通过独立审计与两周期重放。
- 拆分前后 10 组对照一致：5 组精确最优解，以及固定 seed=42、迭代次数为 0 的 5 组 ALNS 初解。
  对比路线、日程、目标值、审计与证明字段，不比较运行耗时；这项检查不代表完整 ALNS 随机搜索或大规模性能评测。
- 逐函数比较迁移前后的语法树，扣除导入路径和共享转换函数改名后，算法函数体保持一致。
- 全库测试 **234 passed / 1 failed**。唯一失败仍为既有 `container_pools.json` 使用 schema 1，读取器要求 schema 2。
  本次没有修改容器池配置、读取器或相应测试。
- 结果、测试日志与核对记录见 [求解器拆分验证](../output/experiments/solver_separation_20260929/verification.json)。

## 数据与模型文件夹整理的验证

- 数据按单批/多批×小中大归位，69 个基础场景、207 个案例全部保留。
- 594 个订单、日程、网络、车队及道路矩阵文件逐字节一致；207 个案例配置与 69 个来源文件仅更新路径。
- 各模型的精确/ALNS 入口迁入独立文件夹，共同求解算法文件逐字节未变。
- 全库 **248 passed**；数据验收 **207/207**，均通过配对检查和连续两周期重放。
- 原全量精确任务仍为暂停状态，171 个完成结果及其冻结输入、源码未改写。
- 迁移前备份、路径映射位于 `archive/dataset_layout_20260929/`；
  [本次校验记录](../output/experiments/dataset_layout_20260929/verification.json) 汇总测试和数据审计。
