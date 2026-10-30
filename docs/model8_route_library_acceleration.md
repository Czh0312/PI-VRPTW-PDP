# 模型八路线库与主问题装配优化

`experiments/runs/model8_trip_library.py` 对乐观单趟路线采用分层 Pareto 标签。固定初始配送集合后，状态包含已访问任务、当前位置与载荷；标签同时比较距离、时钟以及每个已送未取订单的有效取箱就绪时刻。只有全部不劣时才支配另一标签。按已访问任务数分层，保证先完成同层支配比较，再扩展下一层。

比较取箱就绪时刻是必要条件。较早到达且距离较短的前缀，仍可能因为送达时刻不同，使后续取箱需要等待更久，不能仅按距离或当前时钟合并。每个任务集合仍保留其完整搜索中的最短可行单趟距离；不使用束宽、标签数量限制或人工成本截断。

`experiments/runs/model8_trip_library_native.py` 与同名 `.cs` 提供可选编译执行方式。辅助程序执行原始完整枚举，保留全部子集、访问顺序、容量、时间窗、先送后取和最短使用时长检查。编译器和输出文件路径由调用者明确提供，不修改 Python 或系统配置。

```python
from experiments.runs.model8_trip_library_native import build_native, enumerate_trip_library

executable = build_native(compiler_path, output_directory / "TripOracle.exe")
stats = {}
library, prefixes = enumerate_trip_library(
    problem, native_command=executable, deadline=absolute_perf_counter_deadline,
    stats=stats, include_paths=False, archive_directory=output_directory / "native_routes")
```

`include_paths=False` 仅省略 Python 列对象中的路径文本；指定归档目录后，每条代表路线的完整路径、距离与任务集合仍保存在二进制证据中。超时不返回不完整路线库，不能将部分枚举当作原问题下界。编译执行支持至多 20 笔订单；Python 标签方式仍可作为可移植实现。编译和进程启动应计入明确声明的计时范围。

`research_model8_shared_relaxation.build_relaxation()` 默认使用分组配送联系；`model8_trip_sync.augment()` 使用分组回收与批次先后联系。共享同一个非空任务集合，或共享同一配送/回收代表任务对的列，在精确任务覆盖约束下至多选择一条。因此，可以用一条组级不等式替代每列相同的联系，而保持所有整数可行解；组级表达还可以加强分数松弛。构造覆盖行时只扫描路线库一次，避免反复遍历整个库。

下界模型仍放松完整业务同步、物理车辆日程和部分时钟条件。它的整数解不直接作为原业务可行方案。完整子问题保留所有原路线顺序与车辆调度；固定任务分区后，消除已固定为零的路线列，只是去掉零变量，不是强制采用乐观最短代表的访问顺序。只有原业务与两周期审计通过的上界和有效全域下界闭合，才能声明原问题最优。

验证范围包括新旧完整路线库的任务集合与距离一致性、独立路径回放、非对称/非度量矩阵、重复位置、分数容量、多箱订单、取箱就绪时刻，以及组级/逐列主问题整数最优值对照。较大实例还应分别记录枚举、证据写盘、装配、优化与完整子问题耗时，不能把枚举提速直接解释成完整精确求解已经同倍提速。
