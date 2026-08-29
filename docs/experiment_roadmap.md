# 当前实验路线

## 1. 目标

实验分成三条证据链：

1. 小规模精确实验验证模型约束和 ALNS 解质量；
2. 大规模 ALNS 实验验证算法可扩展性与各机制的边际价值；
3. LaDe-D 案例把真实道路里程接入生命周期成本模型。

当前实验只使用 `data/zhou_later_pickup/` 和 `data/real_case/`。Model 1–7 均可参加小规模精确验证和适用的 ALNS 对比；M7 精确枚举只建议用于很小实例。

## 2. 小规模精确验证

数据使用 Zhou Set1 的 7/10/12 客户实例。建议按模型能力分层：

| 对比 | 目的 | 主要指标 |
| --- | --- | --- |
| M1 vs M2 | 自由分仓对纯配送的影响 | objective、assignment、runtime |
| M3 vs M4 | 自由分仓对配送—回收的影响 | objective、assignment、runtime |
| M3 vs M5 vs M6 | 空箱接力/交接的影响 | objective、relay events、runtime |
| M4/M6 vs M7 | 自由分仓与异步交接联合价值 | assignment、relay events、objective、runtime |
| Exact vs ALNS | 启发式解质量 | proven optimum、ALNS gap、命中时间 |

精确求解达到时间限制时必须同时保存 incumbent、best bound 和 gap，并标记为未证明。不得将受限候选集合上的 `OPTIMAL` 当作原问题全局最优。

当前批处理入口：

```powershell
python experiments/runs/run_mainline_small_exact.py `
  --groups small --scenarios standard_same_day --models 1 2 3 4 6 7

python experiments/runs/run_small_alns_time_to_exact.py --help
python experiments/runs/run_model5_model6_exact_comparison.py --help
```

## 3. 中大规模 ALNS

数据使用 Zhou Set2 的 100/200 客户实例。比较 M1、M2、M3、M4、M6、M7，并在相同场景、时间预算和随机种子下报告：

- 可行率；
- 总距离及 E1/E2 分项；
- 多 seed 均值与标准差；
- M2 相对 M1、M4 相对 M3 的自由分仓收益；
- M6 相对 M3 的交接收益和实际交接次数；
- M7 的最终分仓、相对同次第一阶段自由分仓解的交接收益和实际交接次数；
- 运行时间。

入口：

```powershell
python main.py compare --scale large --scenario standard_same_day `
  --time 10 --seed 42 --models 1 2 3 4 6 7
```

比较以相同时间预算为主。不同模型单次迭代的计算量不同，不能只用相同迭代数判断优劣。

Model 7 已完成一组标准基准：Set1 的22个实例按120秒/例运行精确法，
10例证明全局最优、7例得到限时可行解、5例限时内无incumbent；Set2的18个
实例按10秒/例、seed=42运行ALNS，17例得到可行解。完整结果见
`output/experiments/model7_exact_and_large_alns_experiment.md`，稳定机器可读结果见
`output/results/model7_exact_and_alns/`。

## 4. 真实案例成本分析

LaDe-D 杭州案例使用 `config/cases/lade_hz_0829.json` 统一组合实例、车辆参数、成本参数和 OSRM 矩阵。需要报告：

- 各路径机制的公里数与运输成本；
- 一次性/可循环方案的累计成本曲线；
- 清洗、追踪、损耗和运输的价值分解；
- 损耗率等关键参数的敏感性；
- 所有情景假设与真实观测字段的边界。

```powershell
python main.py cost --case config/cases/lade_hz_0829.json --time 10 `
  --output output/economic_analysis_lade_real_0829
```

## 5. 报告规则

- 机器可读的 CSV/JSON 与 Markdown 结论必须来源一致；
- 结果目录保留数据集、场景、模型、seed、预算和时间戳；
- 只在 `proven_optimal=True` 时计算相对精确最优 gap；
- 未触发交接时应如实报告“0 次”，不能仅根据模型具备该机制推断收益；
- Zhou 坐标不直接换算为真实人民币运输成本；真实成本采用 LaDe-D OSRM 公里矩阵；
- 旧版“固定分仓逐轮中转试探”Model 7 研究记录只在 `archive/` 中追溯，不与当前联合 Model 7 汇总混用。
