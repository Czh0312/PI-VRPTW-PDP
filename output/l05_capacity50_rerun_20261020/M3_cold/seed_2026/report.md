M3 固定批次运行结果

包装：循环；批次数：1。
状态：INFEASIBLE。
独立审计：False；已证明最优：False。
距离：None；循环箱池：100。
已证明不可行：True；证明范围：static_necessary_conditions_v1。
必要容器池下界：100；必要条件通过不代表已找到可行解。

| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |
| --- | ---: | ---: | ---: |

连续两个大周期重放：False。

时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / (60 × time_tick_subdivision)，细分倍率缺省为1。

必要条件不可行证据：
```json
[
  {
    "code": "UNSPLIT_E1_DELIVERY_CAPACITY",
    "satellite_id": "S3",
    "batch_id": "b1"
  }
]
```
