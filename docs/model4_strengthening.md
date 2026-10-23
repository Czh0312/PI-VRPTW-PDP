# M4容量与连通性强化

2026-10-23。针对小规模M4的下界偏弱问题，在原完整MILP上增加有效不等式。
网络、订单、自由选仓、车辆、趟次上限和业务时间窗保持原样；目标仍为两级路径总距离。
实现位于 `models/routing/model_4/exact/strengthening.py`。

## 二级客户集合容量约束

取非空客户集合S，包含这些客户的所有订单及其配送、回收任务。
令E(S)为全部二级路线从仓库或S外任务跨入S的弧数。
令D_w(S)、D_v(S)为配送总重量和体积，P_w(S)、P_v(S)为回收总重量和体积，
Q_w、Q_v为二级车辆容量。加入：

```text
E(S) >= max(1,
            ceil(D_w(S)/Q_w), ceil(D_v(S)/Q_v),
            ceil(P_w(S)/Q_w), ceil(P_v(S)/Q_v))
```

容量为零的项不作除法，由原容量约束和预检处理。实现使用很小的保守数值松弛，避免浮点比值接近整数时
错误多要求一次进入；这只可能使不等式略弱，不会增加车辆需求。

证明：路线每次跨入S后，在离开S前能配送的货物不超过车辆进入时所载货物，因此每段分别受重量和体积容量限制。
对全体段求和并向上取整，得到配送部分。回收箱必须通过跨出S的弧运走，同样受容量限制。
闭合路线的跨入和跨出次数相等，因此回收部分也能写在同一进入次数左侧。服务非空客户集至少跨入一次。
配送和回收可以在不同车辆或不同趟次，不要求每位客户的送取由同一条路线承担。

客户数不超过12时枚举所有非空集合；更大实例只枚举规模1、2、3及完整集合，避免指数建模。
共享二级内部弧只计一次，仓库边界弧按实际路线槽位累计，适用于压缩和非压缩表达。
该证明不依赖距离对称性、三角不等式或最近仓假设。

## 一级仓库集合连通性

对非空仓库集合T及其中每个仓s，令a_s表示仓库启用：

```text
一级路线跨入T的总弧数 >= a_s
一级启用路线数 >= ceil(总配送重量/一级重量容量)
一级启用路线数 >= ceil(总配送体积/一级体积容量)
```

每条一级路线起终点都在总仓。启用的仓库必须由总仓连通的路线服务，且全部配送货物必须由启用车辆运入，
因此上述不等式对原合法方案成立。仓库集合同时包含该仓配送和回收节点。

## 诊断开关与验证

另一项单独消融是车辆启用容量联动，位于 `activation_capacity.py`。对每条实际趟次分别加入：

```text
该趟配送总重量/体积 <= 对应容量 × used
该趟回收总重量/体积 <= 对应容量 × used
```

一趟车的配送货物全部从仓库装出，回收空箱全部随车带回，故两个方向分别成立。
不能将配送与回收总量相加后约束为一份容量，因为同一车厢容量可以在卸货后用于装回空箱。
这项表达由 `run_model4_activation_trial.py` 独立比较，不修改正在执行的容量集合对照版本。

建造接口及M4独立 `solve_exact` 的 `strengthening` 参数区分原表达 `none`、辅助顺序界 `bounds`、
容量与连通性 `capacity`、二者组合 `combined`、逐趟联动 `activation`、集合与逐趟联动 `capacity_activation`。
默认保留 `none`：冷启动试验中集合容量约束虽提高下界，也可能拖慢好路径的发现，不能直接承诺默认提速。
顺序界版本用于消融，不预设叠加后性能一定更好。

命令行显式设置 `--model 4 --solver exact --milp-formulation compact --m4-strengthening capacity_activation`，
并使用原案例、时间预算及新的输出目录。其他模型或ALNS调用会拒绝这一专用参数。
公共Python入口也支持 `solve_routing(model=4, method='exact', ..., m4_strengthening='capacity_activation')`。
结果的 `info.solver_config.strengthening` 保存实际启用的约束类别及数量。

短时ALNS与强化MILP的组合由 `run_single_batch_warm_start_trial.py` 的
`--m4-strengthening capacity_activation --modes alns --total-seconds 300 --alns-seconds 30` 独立评估。
初解生成时间计入总预算，不能将它与纯表达强化混为同一项收益。

`experiments/runs/run_model4_strengthening_trial.py` 在相同种子、单线程、compact和总预算下比较表达，
不用外部初解、外部下界或观察回调。每次保存完整解、Gurobi日志、源码ZIP、配置哈希与独立审计。

- [30秒筛选数据](../output/inspections/m4_strengthening_screen_20261023_v1/summary.csv)
- [300秒成对试验数据](../output/experiments/m4_capacity_strengthening_300s_20261023_v1/summary.csv)
- [18项固定合法方案的保留检查](../output/inspections/m4_strengthening_feasibility_20261023_v1/verification.json)
- [组合版9项固定方案保留检查](../output/inspections/m4_capacity_activation_preservation_20261023_v1/verification.json)
- [逐趟联动的30秒消融](../output/inspections/m4_activation_screen_20261023_v1/summary.csv)

定向测试为 `tests/routing/test_model4_strengthening.py`，验证重复订单、零箱重及非对称非度量距离下，
强化前后精确最优值一致，提取结果通过独立双周期重放。有限实例测试是实现检查，不能替代上述有效性证明。

当前7顾客冻结版的完整组合复测使用 `experiments.runs.run_small7_m4_combined_check`：
9例统一30秒ALNS初解、`capacity_activation`、单线程和种子42，共享每例300秒总预算。
9/9证明最优，最长总耗时92.57秒；冷启动原表达为7/9证明最优。
该组合同时改变初解与表达式，两项影响不能单独从这组结果拆分。
逐例对照、源码和输入哈希、9份独立重放及S01/M3的补充下界证明见
[7顾客重测报告](../output/experiments/small7_solver_comparison_20261023_v1.md)。
