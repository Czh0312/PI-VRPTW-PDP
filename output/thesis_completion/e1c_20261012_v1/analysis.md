# 阶段结果分析

阶段 e1c_diagnostic；完成 90/90，审计可行 90/90。
状态：全部首次尝试已有终态。

费用均值仅使用审计有效运行；可行率以所有预先登记运行为分母。标准差采用样本标准差，单样本留空。
同源嵌套案例必须按 family 解释，不将种子或嵌套子集作为独立经验样本。

- `tables/per_instance_statistics.csv`：逐实例、模型、版本和预算的费用分布及有效样本数。
- `tables/paired_versions.csv`：匹配实例/模型/方法/种子/预算的新旧版本对照；双方有效时才算差异。
- `tables/cost_components.csv`：总费用、分量、N/Q、K 与实际配送批数。
- `tables/search_diagnostics.csv`：构造和评价耗时、违规类型、各算子尝试/改进；评价有效率与移动接受数分开。
- `tables/ablation_contrasts.csv`：A−B、A−C、C−D、B−D、A−D 和 A−B−C+D；不将边际差相加作独立贡献。
- `figures/`：实际预算使用率及外层确认的全局费用轨迹；未知最优时不称最优误差。
