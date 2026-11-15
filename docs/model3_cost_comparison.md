# 六模型的每期成本目标

正式算例的 MILP 和 ALNS 按同一运营周期的总成本选择方案。

## 共同目标

六个模型使用 `JointCostProfile`。两种包装方案都按各自作业计算两级运输、占用工时、启用车辆和趟次成本。
纸箱费按当期消耗量乘以纸箱购价。循环箱费按配置的实体箱池乘以单箱日使用费，清洗和追踪使用单独的费率。
每组对照共用同一 `economic_config` 文件、计费周期和单位。

基准的运输项为 `0.1*D1 + 0.05*D2`。M1、M2 的包装项为 `5*Q`，M3–M6 的箱池和清洗项为 `5*N + 0.01*Q`。
数值为模拟费率。M3、M4 给定箱池 `N`，M5、M6 求解箱池规模。两级运输费率不同，分仓及路线按加权运输成本评价。

模型 3 固定分仓、单批次和初始箱池。命令行默认由每个前置仓在当批空箱全部卸毕后一次交接给一级车。装货遵循订单释放时间，配送和取箱遵循顾客时间窗，次日首单释放前完成回仓清洗和车辆转场。
历史固定截点的实验可以用 `--fixed-work-window` 复现。要验证模型 5 在固定批内分界下的退化关系，须对齐该分界和其余物理条件。
通常将固定箱池设为总需求箱次 Q，额外配置的库存按输入量计费。

## 使用方式

案例配置增加：

```json
{
  "model": 3,
  "no_work_window": true,
  "economic_config": "data/benchmark27_models_1_6_v1/shared/small/S01/economic.json"
}
```

可运行示例为 `config/experiments/examples/single_batch_cost_demo.json`。
使用 `main.py route --case <案例>` 运行，可传入 `--economic-config <共同费用文件>`。

每期费用应使用同一口径。旧批量对照保存在[历史结果索引](../output/history/pre_renumbering_20261113/README.md)。

正式运行的 `info.objective` 是每期总成本，`total_distance` 记录物理总距离。同时保存
`cost_components`、费用哈希及固定箱池。独立审计重新检查路线、连续两个周期和全部费用。
精确求解的上下界和最优性针对成本目标；ALNS 只报告所找到的可行成本。
集中交接且工时费为零时，输出按既定路线推算最早可行的时间表。收齐时刻和一级取箱时刻见审计的 `consolidated_handoffs`。工时计费时，输出采用求解器的时间表。

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
