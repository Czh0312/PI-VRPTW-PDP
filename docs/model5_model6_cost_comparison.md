# 模型五与模型六的成本实验

模型五保留固定分批、固定箱池和距离目标。每次求解后，独立审计原始路线与时间表，再使用与模型六完全相同的 `JointCostProfile` 计算周期费用。模型六采用均匀周期、共同分界的成本联合优化。输入订单、道路、车队、原始周期和经济配置须一致；模型六重新划分日历是明确的实验决策范围。

2026-10-05完成的四案例、32次尝试见[分析结果报告](../output/model6_comparison/20261005_m5_m6_cost_comparison/analysis_report.md)。31次方案通过独立审计。按M5原配置箱池，M6节省21.92%～34.74%；按M5原调度最低箱池参考，最大案例M6反而增加12.90%。报告包含成本分解、重复种子统计、求解证明范围和原始数据链接。

## 在当前仓库生成并运行

已提交的历史模型六输入可能含生成机器的绝对路径。在本机从相对路径的原始场景重新生成一套输入，再运行对比，保留历史数据。以下输出目录须是新的或空的。

```powershell
python -m tools.data.build_model6_static_suite --uniform-cycle --batch-count-candidates 1 2 3 --families mechanism_cutoff_choice zhou_Set1_7-2-1 lade_0823_10aoi lade_0823_30aoi --output output/model5_model6_cost_inputs
python -m experiments.runs.run_model6_uniform_comparison --manifest output/model5_model6_cost_inputs/manifest.json --exact-time 30 --time 15 --iterations 30000 --seeds 42 43 44 --workers 2 --timeout-overhead 45 --output output/model5_model6_cost_study
```

这组包含四个代表场景。每模型每场景一次精确求解、三个ALNS种子，共32次尝试。精确求解需要可用的 `gurobipy` 和许可证；只运行ALNS可添加 `--solvers alns`，只运行精确法可添加 `--solvers exact`。M6的所有候选K共享一次调用预算，不能将其每个K预算乘倍数后再与M5比较。

当前Windows机器的Python没有全局安装 `gurobipy`，实验使用现有Gurobi 11安装包里的Python 3.11模块，复制到被Git忽略的 `output/.model6_runtime/`。在同一PowerShell会话设置 `$env:PYTHONPATH = (Resolve-Path output/.model6_runtime).Path` 后再执行含精确法的命令；该目录只放 `gurobipy` 包，避免整个Gurobi自带标准库覆盖Anaconda的Python库。

## 输出与计价

- 每次运行的 `result.json` 保留求解结果，`independent_audit.json` 保留两周期物理重放。
- `comparison_cost.json` 保存全部费用分项、计价单位、参数哈希、箱池规模和单列购置投入。
- `cost_report.md` 展示两模型总成本、节省额、节省比例及各方法全部种子的统计。
- `cost_summary.json` 保留代表方案、证明目标、逐对结果及统计；`cost_comparison.csv` 可用于逐例对比。
- `cost_components.csv` 保存每次有效运行的费用分项；`cost_pairs.csv` 保存同方法同种子的成本和失败状态。
- `rows.json`、`rows.csv` 保留全部尝试；`experiment.json`、`inputs/`、`source_snapshot/` 保存预算、输入和源码。

主对比使用模型五实际配置的箱池。另列 `schedule_minimum_pool_reference`：在原路线和时间表不变时，按审计得到的最低需箱量重新计价，只有箱池持有费与单列购置投入发生变化。这不是重新求解的模型五，也不是箱数全局最小的证明。

模型五代表方案按最短距离选取，同距离时按成本、种子和方法排序；模型六按最低已观察成本选取。报告分别保存各方法的最低、平均、最高成本及样本标准差，单次运行的标准差留空。节省额为 `M5成本−M6成本`，节省比例为该差值除以M5成本；负值表示模型六成本更高，分母为零时比例留空。

原费用均为合成单位，结果不是人民币实测节约。模型五的最优证明只针对距离；模型六只有在所有配置K解决且审计通过后才有成本最优证明。两模型日历和目标同时变化，短预算对比不能单独归因于分批，也不能视为收敛或统计显著结论。

## 给已有模型五结果计价

```powershell
python main.py cost --case output/model5_model6_cost_study/cases/0_M5_distance.json --route-result output/model5_model6_cost_study/00_M5_distance_alns_42/result.json --output output/model5_repriced
```

案例里的 `economic_config` 会被自动读取，也可以用 `--economic-config` 显式指定同口径配置。旧的 `cost_config` 格式仍按原计价规则使用。

已有实验目录中的 `comparison_cost.json` 可重新汇总，不需要再次求解：

```powershell
python -m experiments.analysis.summarize_model6_costs --directory output/model5_model6_cost_study
```

本次完整实验另外保存了环境和检查记录，可用以下命令重建详细报告、分析数据和两张成本图，无须重新求解：

```powershell
python -m experiments.analysis.analyze_model5_model6_costs --directory output/model6_comparison/20261005_m5_m6_cost_comparison
```
