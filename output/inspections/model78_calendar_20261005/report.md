# 模型7/8实现验收（2026-10-05）

模型7：等长批次、逐批独立分界。模型8：不等长批次、逐批独立分界。均提供精确MILP和ALNS。

两轮不重复测试共180项通过：原M6/编号/范围回归113项；新增日历与成本/共享数据回归67项，其中49项为新增M7/M8测试。后者JUnit见pytest.xml。
包含极小整数时间网格的所有固定日历及截点枚举，对照完整MILP最优值；以及审计篡改、释放时间、清洗复用、求解器隔离、初解保留和限时证明状态。

以下10次CLI运行全部通过保存后重新独立审计和连续两个周期重放，原输入与冻结源码SHA256核对通过。机制例精确总预算20秒、ALNS总预算2秒/1500迭代；Zhou精确10秒、ALNS3秒/3000迭代；LaDe仅ALNS3秒/3000迭代。均为种子42。
部分执行与测试并行，仅作为工程执行检查；不是算法性能或多种子收敛实验。费用为合成单位。

| 运行 | 状态 | 成本 | 周期划分K | 配送批数 | 箱池 | 最优已证明 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| [lade_0823_10aoi_m7_alns](lade_0823_10aoi_m7_alns/report.md) | FEASIBLE | 1055.856267 | 1 | 1 | 131 | False |
| [lade_0823_10aoi_m8_alns](lade_0823_10aoi_m8_alns/report.md) | FEASIBLE | 1049.520700 | 1 | 1 | 131 | False |
| [m7_alns](m7_alns/report.md) | FEASIBLE | 80.000000 | 2 | 2 | 1 | False |
| [m7_exact](m7_exact/report.md) | OPTIMAL | 80.000000 | 2 | 2 | 1 | True |
| [m8_alns](m8_alns/report.md) | FEASIBLE | 80.100000 | 2 | 2 | 1 | False |
| [m8_exact](m8_exact/report.md) | OPTIMAL | 80.000000 | 2 | 2 | 1 | True |
| [zhou_Set1_7-2-1_m7_alns](zhou_Set1_7-2-1_m7_alns/report.md) | FEASIBLE | 577.468496 | 1 | 1 | 12 | False |
| [zhou_Set1_7-2-1_m7_exact](zhou_Set1_7-2-1_m7_exact/report.md) | OPTIMAL | 546.413786 | 1 | 1 | 12 | True |
| [zhou_Set1_7-2-1_m8_alns](zhou_Set1_7-2-1_m8_alns/report.md) | FEASIBLE | 577.470163 | 1 | 1 | 12 | False |
| [zhou_Set1_7-2-1_m8_exact](zhou_Set1_7-2-1_m8_exact/report.md) | OPTIMAL | 537.315453 | 2 | 1 | 12 | True |

机制例M7与M8示例的订单不同，不能直接据两者成本比较模型收益。等长独立分界与共同分界的同输入测试为80.0对80.2；晚备货机制例在等长K=2下不可行，不等长后可行。
Zhou同输入M7/M8精确解均证明配置K=1/2/3范围内最优；M8可通过保留无配送区间来推迟实际发运，周期划分数与实际配送批数不可混淆。两种3秒ALNS均未达到对应精确最优值。

实现与命令见[设计说明](../../../docs/model7_model8_calendar_design.md)。未运行全量数据集或正式大规模性能评测。
