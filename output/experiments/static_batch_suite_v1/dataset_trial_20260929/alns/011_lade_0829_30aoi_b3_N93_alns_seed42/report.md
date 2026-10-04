多批次有限容器池运行结果

状态：INFEASIBLE。
独立审计：False；已证明最优：False。
距离：None；固定容器池：93。
已证明不可行：True；证明范围：static_necessary_conditions_v1。
必要容器池下界：144；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |

连续两个大周期重放：False。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / 60。

必要条件不可行证据：
```json
[
  {
    "code": "POOL_BELOW_NECESSARY_BOUND",
    "pool": 93,
    "lower_bound": 144
  }
]
```
