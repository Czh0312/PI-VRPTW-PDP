# 模型3的成本目标对照

模型3新增显式 `objective_mode="cost"`，用于与模型5、6、7、8比较。
MILP 和 ALNS 都直接按完整成本选择方案；不是先求最短距离，再对所得路线计价。
原距离模式仍是默认值，用于模型1/3、模型3/4等既有对照。

## 共同目标

模型3成本模式与模型5–8使用同一个 `JointCostProfile`，包含箱池、一级/二级运输、
占用工时、启用车辆、趟次、清洗、追踪及磨损费用。具体项目是否启用由同一份
`economic_config` 决定；不得在模型间换用不同费用文件或不同租赁/购置口径。
模型3与模型5的固定日历 MILP 共用成本表达式构建器。

当前基础费用为 `0.1*D1 + 0.05*D2 + 5*N + 0.02*Q`。
单批次、完整回收且固定箱池 `N=Q` 时，等价于优化 `0.1*D1 + 0.05*D2`；
一级、二级运输费率不同，不能用原来的 `D1+D2` 最优解替代成本优化解。
若实验使用箱池租赁系数1等其他情景，所有比较模型必须同时读取该情景的费用文件。

模型3仍固定客户所属仓、单批次起止、批内分界和输入箱池，不增加日历或分仓决策。
通常将固定箱池设为总需求箱次Q；额外配置的库存也会如实计费，不能在报告时改成Q。
模型8即使选单批次，仍可优化批内分界；要验证与模型3的退化关系，须对齐该分界及其余物理条件。

## 使用方式

案例配置增加：

```json
{
  "model": 3,
  "objective_mode": "cost",
  "economic_config": "config/experiments/benchmark27_v1/economic.json"
}
```

可运行示例为 `config/experiments/examples/single_batch_cost_demo.json`。
对既有案例可使用 `main.py route --case <案例> --objective-mode cost`；案例须已提供费用文件，
也可用 `--economic-config <共同费用文件>` 指定。对模型3显式传入此参数会启用成本模式，
除非同时显式指定 `--objective-mode distance`。

批量正式对照使用 `experiments/runs/run_formal_benchmark27.py prepare`，增加
`--models 3 5 6 7 8 --m3-objective-mode cost`，其余预算、种子、输出目录和预检证据参数照常提供。
准备程序生成独立成本配置和参数契约，登记实际目标，不改写冻结数据集或历史结果。
原 `run_spacetime_quality.py` 的既有队列不自动切换目标；租赁情景对照须另建一致费用的成本实验。

结果中 `info.objective` 是总成本，`total_distance` 是物理总距离；同时保存
`cost_components`、费用哈希及固定箱池。独立审计重新检查路线、连续两个周期和全部费用。
精确求解的上下界和最优性针对成本目标；ALNS 只报告所找到的可行成本。
成本模式保留实际求解时间表，不再对精确解做可能改变工时成本的最早时刻归一化。

针对性回归文件：`tests/routing/test_model3_cost_objective.py`。

## 各规模统一的精确求解加速

M3 默认共享二级内部弧，并加入逐趟配送/回收载荷与启用联系、由原容量推导的必要趟数。
这些有效联系适用于所有订单规模，不按顾客数切换。

输入型下界按仓组合：每仓先求多项式弧流LP下界，再在自动分配的资源预算内枚举完整放松车次集合。
完整仓使用整数集合划分下界，未完成仓保留LP或解析下界；部分车次库的最小值不作下界。
初解候选由确定性插入及已枚举车次生成，通过完整模型副本尝试调度。所有准备计入同一总时间预算。
固定分仓、日历、箱池、费用和整数时钟由原模型保留，不复用历史方案或最优值。

最终 `proven_optimal` 仍要求完整模型的原生最优状态与独立业务审计通过，不能由放松问题
或受任务分组限制的副本单独发布。原生主模型证据、输入型下界、内部初解与准备耗时分别保存。
原expanded冷启动基线可通过 `models.routing.model_3.exact.solve_exact(..., route_strengthening=False)` 复现。
正确性与超时回退测试见 `tests/routing/test_model3_route_partition.py`。
各项下界的有效性与统一资源规则见[通用加强说明](model3_general_acceleration.md)。
