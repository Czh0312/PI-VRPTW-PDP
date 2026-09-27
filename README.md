# 2E-VRPTW-PDD

本项目用于求解两级带时间窗的配送—延后回收问题，并对一次性包装与可循环容器方案进行经济成本分析。当前代码分为两条彼此解耦的主线：

- `models/routing/`：路径模型、精确求解器和 ALNS；
- `models/cost/`：基于路线里程与业务参数计算生命周期成本。

当前范围是 Model 1–8。Model 7 在 Model 4 自由分仓的基础上加入 Model 6 的同质空箱异步交接。Model 8 联合优化两级路线、配送回收安排与空箱容量协作，提供完整联合精确求解和无需 MIP 的 ALNS 搜索。

## 快速开始

```powershell
# 默认案例：Zhou 小规模数据，Model 6 ALNS
python main.py

# 指定路径案例、模型和求解方式
python main.py route --case config/cases/zhou_small_demo.json --model 4 --solver alns --time 30

# Model 7 原分仓枚举精确求解
python main.py route --model 7 --solver exact --exact-time 300

# LaDe-D 杭州代表日成本分析
python main.py cost --case config/cases/lade_hz_0829.json --time 10 `
  --output output/economic_analysis_lade_real_0829

# Zhou 数据集批量比较
python main.py compare --scale small --scenario standard_same_day --time 5

# 功能回归测试；没有 Gurobi 时相关精确测试自动跳过
python -m pytest -q
```

依赖安装：

```powershell
python -m pip install -r requirements.txt
```

Gurobi 精确求解需要本机安装 `gurobipy` 并配置有效许可证；多数 ALNS、数据处理与成本分析不依赖 Gurobi。M8 ALNS 为单个联合搜索，无需 Gurobi；精确入口直接求解完整联合 MILP。

## 当前模型

| 模型 | 问题机制 | 完整模型 | 精确入口 | ALNS |
| --- | --- | --- | --- | --- |
| Model 1 | 固定分仓、纯配送 | `variants/model_1/model.py` | `variants/model_1/exact_solver.py` | `alns/variants/model_1.py` |
| Model 2 | 自由分仓、纯配送 | `variants/model_2/model.py` | `variants/model_2/exact_solver.py` | `alns/variants/model_2.py` |
| Model 3 | 固定分仓、配送与延后回收 | `variants/model_3/model.py` | `variants/model_3/exact_solver.py` | `alns/variants/model_3.py` |
| Model 4 | 自由分仓、配送与延后回收 | `variants/model_4/model.py` | `variants/model_4/exact_solver.py` | `alns/variants/model_4.py` |
| Model 5 | 空容器单次车辆间接力 | `variants/model_5/model.py` | `variants/model_5/exact_solver.py` | `alns/variants/model_5.py` |
| Model 6 | 同质空箱异步事件地点交接 | `variants/model_6/model.py` | `variants/model_6/exact_solver.py` | `alns/variants/model_6.py` |
| Model 7 | 自由分仓 + 同质空箱异步交接 | `variants/model_7/model.py` | `variants/model_7/exact_solver.py` | `alns/variants/model_7.py` |
| Model 8 | 两级路径、配送回收与空箱容量协作联合优化 | `variants/model_8/model.py` | `variants/model_8/exact_solver.py` | `alns/variants/model_8.py` |

表中路径均位于 `models/routing/`。Model 1–8 均按统一结构放置：

- `model.py` 保存当前完整数学模型；
- `exact_solver.py` 保存对应的严格精确求解入口；
- 公共数据结构、任务构建和求解工具放在 `common/`；
- 公共 ALNS 引擎与模型专属算子放在 `alns/`。

历史列生成、分支定价、时间扩展、分解算法和旧实验探针均在 `archive/`，不会被正式入口调用。

## 数据与参数

- `data/zhou_later_pickup/`：当前算法验证数据。小规模为 7/10/12 个客户，大规模为 100/200 个客户，并包含不同回收情景。坐标单位不直接等同于真实公里。
- `data/real_case/`：LaDe-D 杭州真实配送记录形成的代表日案例，包含 30 个 AOI 和 OSRM 道路距离/时间矩阵。回收量与回收时间窗属于明确标注的情景设计。
- `config/routing/validation/`：理论验证车辆与容量参数。
- `config/routing/real_cases/`：真实案例车辆参数。
- `config/cost/cases/`：经济成本参数。
- `config/algorithms/`：ALNS 与精确求解预算。
- `config/cases/`：组合数据、矩阵和参数的案例清单。

## 精确解口径

“找到可行解”和“证明全局最优”严格区分。只有返回信息中的 `proven_optimal=True` 才能计为精确解：

- 完整 MIP 返回 `OPTIMAL`；或
- Model 5/6 的有效下界证书与可行上界闭合；或
- 证书未闭合时，回退的完整联合 MIP 返回 `OPTIMAL`。

达到时间限制的 incumbent 应报告目标值、下界和 gap，但不能写成精确最优。详细契约见 [精确求解架构](docs/exact_solver_architecture.md)。

Model 7 使用完整分仓枚举、松弛证书和完整 Model 6 子问题进行精确求解。

Model 8 当前为联合优化：E1/E2 路线、配送/回收归属与顺序、用车和空箱交接均参与决策。
精确入口直接求一个完整 MILP，只有闭合全问题才设置 `proven_optimal`；ALNS 不先求 M3。
当前交接规则下，精确可行域与 M6 相同，M8 的重点是容量协作联合邻域。
见 [M8 设计文档](docs/model8_capacity_cooperation.md)。历史两阶段实现位于 `model_8/two_stage_v2/`，
更早版本位于 `model_8/legacy/`；历史实验不能作为当前联合版本的结果。

## 最新精确求解与 ALNS 结果

2026-09-13 启动的六模型优化：
[详细分析](docs/solver_optimization_20260913.md)与
[最新精确/ALNS结果总表](output/experiments/solver_optimization_20260913/report.md)。
新批次包含同预算基线对照、22 个小规模实例及18 个大规模实例，
原始路线、上下界、证明状态和源代码快照均保留。总表顶部标明批次是否完成。

以下为保留的历史批次：

M4 默认交换邻域优化后的[更新总表](output/experiments/small_alns_updated_20260908/report.md)
覆盖 22 个实例、3 个随机种子、六个模型：M4 按原种子重跑 66 次，其余五个模型沿用
原批次 330 次记录，全部 396 条路线重新通过独立审计。报告明确标注批次来源，
并更新模型间配对和 M4/M7 的共同精确样本比较。

算法改动的独立同预算验证见 [M4 交换邻域对照](output/experiments/m4_exchange_schedule_20260908/report.md)。


## 项目目录

```text
main.py                  统一命令行入口
cli/                     route / cost / compare 子命令
models/routing/          路径模型、精确求解与 ALNS
models/cost/             生命周期成本模型
configuration/           配置加载与路径解析
config/                  分层参数与案例清单
data/                    Zhou 与 LaDe-D 当前数据
experiments/             当前实验运行和汇总脚本
tests/                   功能回归测试
tools/                   数据构建、审计与绘图工具
reporting/               路线和结果展示
output/                  当前数据对应的结果
docs/                    当前模型和实验说明
reference/               文献、参数出处和 Zhou 原始参考资料
archive/                 历史代码、旧文档和旧证据
```

更完整的职责说明见 [项目总览](docs/project_overview.md)，实验安排见 [实验路线](docs/experiment_roadmap.md)，真实案例口径见 [LaDe-D 成本分析](docs/real_case_cost_analysis.md)。
