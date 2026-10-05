# 模型6求解检查：修改前后配对验证

两版均从零开始，使用相同物理输入、均匀分批规则、K=1/2/3、费用参数、时间上限、迭代上限和种子；逐次串行运行，交替先后顺序。有效方案由当前独立审计重放。

| 算例 | 方法/种子 | 原成本 | 新成本 | 原箱数→新箱数 | 原状态→新状态 |
| --- | --- | ---: | ---: | --- | --- |
| mechanism_cutoff_choice | exact/42 | 108.117 | 108.117 | 5→5 | OPTIMAL→OPTIMAL |
| mechanism_cutoff_choice | alns/42 | 116.380 | 116.380 | 6→6 | FEASIBLE→FEASIBLE |
| mechanism_cutoff_choice | alns/43 | 117.014 | 115.380 | 6→6 | FEASIBLE→FEASIBLE |
| zhou_Set1_7-2-1 | exact/42 | 558.598 | 558.598 | 12→12 | OPTIMAL→OPTIMAL |
| zhou_Set1_7-2-1 | alns/42 | 565.971 | 565.598 | 12→12 | FEASIBLE→FEASIBLE |
| zhou_Set1_7-2-1 | alns/43 | 565.598 | 565.598 | 12→12 | FEASIBLE→FEASIBLE |
| lade_0823_10aoi | exact/42 | 873.282 | 791.762 | 82→73 | FEASIBLE→FEASIBLE |
| lade_0823_10aoi | alns/42 | 840.916 | 910.798 | 68→68 | FEASIBLE→FEASIBLE |
| lade_0823_10aoi | alns/43 | 850.803 | 891.875 | 68→68 | FEASIBLE→FEASIBLE |
| lade_0823_30aoi | exact/42 | 无可行解 | 无可行解 | None→None | TIME_LIMIT→TIME_LIMIT |
| lade_0823_30aoi | alns/42 | 1533.262 | 1590.734 | 146→146 | FEASIBLE→FEASIBLE |
| lade_0823_30aoi | alns/43 | 1493.656 | 1644.705 | 176→176 | FEASIBLE→FEASIBLE |

修改仅加强求解：加入路线行驶、服务及卸箱时间的显式下界；ALNS增加清洗赶上下一批的临界点、共同分界的局部细化和较晚回收的初始化；统计覆盖所有K。费用和物理约束未放宽。

限时结果不能证明算法普遍优越，箱数变化不等于成本变化。精确模型小例可检查最优目标是否一致；大例仍需更充分的求解预算。

Material Passport：academic-research-suite/experiment-agent；validate；ANALYZED；model6_search_review_v1。11项解释检查覆盖Simpson、生态、Berkson、碰撞变量、基准率、均值回归、幸存者、多重搜索、分叉路径、相关与因果、反向因果；逐例逐种子列全量结果，不作显著性、总体性能或因果结论。
