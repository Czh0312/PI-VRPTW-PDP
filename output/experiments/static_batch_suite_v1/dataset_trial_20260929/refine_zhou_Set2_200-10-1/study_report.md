静态固定分批：容器池与算法验证

Material Passport：Origin Skill = academic-research-suite / experiment-agent；Origin Mode = run；Verification Status = PARTIALLY_VERIFIED（逐次运行状态与审计见表；不外推性能或经济结论）。

共 2 次运行，2 个解通过独立审计。
同一场景固定订单、批次、车队、窗口及清洗时间，只改变容器池 N。
必要条件通过不证明可行；启发式未找到解、超时或异常均不证明不可行。

| 场景 | 必要池下界 | 最小可行测试池 | 已得时间表最小池 | 已证明最小池 | 未解决测试池 |
| --- | ---: | ---: | ---: | ---: | --- |
| 000_zhou_Set2_200-10-1_b3 | 90 | 90 | 90 | 90 | [] |

最小池证明仅在有效解与必要下界闭合时成立；下界包含已证不可行 N 的单调性推论，证明限于当前时间精度、固定分批及趟次上限。

[逐次运行与 gap](report.md) · [算法汇总](algorithm_summary.csv) · [同池单批/多批对照](batch_comparisons.csv) · [逐批清洁库存](clean_stock.csv) · [各仓冻结量](cutoff_inventory.csv)

算法平均距离只统计有效解，并同时报告全部尝试的可行率；不同清洗时间分别列场景。单批/多批仅匹配相同家族、N、清洗时间、算法及种子，缺少单批运行保留为 NOT_RUN。
manifest.json 在求解前保存完整运行矩阵、预算、输入与源码哈希；工作进程读取冻结副本。
