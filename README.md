# 2E-VRPTW-PDD

本项目用于求解两级带时间窗的配送—延后回收问题，并对一次性包装与可循环容器方案进行经济成本分析。当前代码分为两条彼此解耦的主线：

- `models/routing/`：路径模型、精确求解器和 ALNS；
- `models/cost/`：基于路线里程与业务参数计算生命周期成本。

当前正式范围是 Model 1–6。Model 7 仅保留旧实现，不纳入当前模型整理、实验主线和文档说明。

## 快速开始

```powershell
# 默认案例：Zhou 小规模数据，Model 6 ALNS
python main.py

# 指定路径案例、模型和求解方式
python main.py route --case config/cases/zhou_small_demo.json --model 4 --solver alns --time 30

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

Gurobi 精确求解需要本机安装 `gurobipy` 并配置有效许可证；ALNS、数据处理与多数成本分析不依赖 Gurobi。

## 当前模型

| 模型 | 问题机制 | 完整模型 | 精确入口 | ALNS |
| --- | --- | --- | --- | --- |
| Model 1 | 固定分仓、纯配送 | `variants/model_1/model.py` | `variants/model_1/exact_solver.py` | `alns/variants/model_1.py` |
| Model 2 | 自由分仓、纯配送 | `variants/model_2/model.py` | `variants/model_2/exact_solver.py` | `alns/variants/model_2.py` |
| Model 3 | 固定分仓、配送与延后回收 | `variants/model_3/model.py` | `variants/model_3/exact_solver.py` | `alns/variants/model_3.py` |
| Model 4 | 自由分仓、配送与延后回收 | `variants/model_4/model.py` | `variants/model_4/exact_solver.py` | `alns/variants/model_4.py` |
| Model 5 | 空容器单次车辆间接力 | `variants/model_5/model.py` | `variants/model_5/exact_solver.py` | `alns/variants/model_5.py` |
| Model 6 | 同质空箱异步事件地点交接 | `variants/model_6/model.py` | `variants/model_6/exact_solver.py` | `alns/variants/model_6.py` |

表中路径均位于 `models/routing/`。Model 1–6 统一采用：

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
