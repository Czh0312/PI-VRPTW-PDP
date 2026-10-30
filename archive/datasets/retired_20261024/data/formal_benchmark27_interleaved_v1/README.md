# 一客一箱：顾客间交错送取时间窗

27个基础算例、216份模型配置；小中大各9例，选例、坐标、货重、每客1箱、
分仓、车队、最低使用时间、服务时间、释放、参考分批、日历和费用保持不变。
仅改变每位顾客的 delivery_ready、delivery_due、pickup_ready、pickup_due。
旧版 `data/formal_benchmark27_one_box_v1` 及其求解结果未修改。

窗口规则见 window_rules.json，固定种子 20261019。每位顾客严格先送后取：
pickup_ready >= delivery_due + service_time + min_use_time。
顾客之间分别扰动服务时刻和窗口宽度，回收任务可与其他顾客的配送任务交错。
不要求所有顾客先全部配送再统一回收，不绑定同车或同趟。

这是以显式可行调度为条件的合成预约场景，不是相互独立的实测时间窗。
可行构造会随机把回收安排到含其他顾客配送的路线中，再以因果图的最迟界约束
随机时间扰动。窗口宽度使用独立哈希流。构造过程只检查可行性，不比较距离、
成本、运行时间、gap或模型排名，不丢弃难例，不改变车队，不更换种子挑结果。
参考调度与模型优化相互独立：见证不是订单约束，也不会自动用作求解初始解。

所有模型在同一基础算例上使用相同物理订单。批次数证书按新窗口重新计算，
未加入人为K<=3限制。7顾客算例仍为每站1辆二级车；本次不增加车辆。
费用仍为0.1D1+0.05D2+5N+0.02Q，等待与工时费用仍为0。
因此新数据提供交错服务的可行空间，不承诺所有最优解都没有等待或都换车取送。

216项入口见 case_index.csv。validation/witnesses.zip 保存可行见证，
validation/feasibility.json 保存逐项独立重放记录，validation/windows.json
保存随机窗口、交错关系和非窗口字段保持检查。可行性不等于最优性，旧求解结果
不能作为本版本的求解结果。

生成：tools/run.ps1 python -Arguments @('-m','tools.data.build_interleaved_benchmark27')
审计：tools/run.ps1 python -Arguments @('-m','tools.audit.audit_formal_benchmark27',
  '--dataset','data/formal_benchmark27_interleaved_v1')
重放：tools/run.ps1 python -Arguments @('-m','tools.audit.certify_formal_benchmark27',
  '--dataset','data/formal_benchmark27_interleaved_v1','--verify')
