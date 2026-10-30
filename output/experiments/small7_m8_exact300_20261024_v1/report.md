# 当前模型8小规模：每例300秒精确求解

完成 9/9；可行 0/9；已证最优 0/9。

冻结正式数据formal_benchmark27_spacetime_v4_1；S01—S09均为7顾客；完整K=1—7。
默认expanded统一MILP、直接Gurobi、单线程、种子42、串行冷启动、MIPGap=0、MIPGapAbs=0。
原始economic.json保持（箱池系数5）；无外部初解、回调或自定义强化。
300秒包含模型入口、建模和优化；调用墙钟另含提取及内部审计，进程和再次独立审计开销另列。
S02首次调用被Windows状态文件共享冲突中断，保留attempt_001和INTERRUPTED_IO记录，另以attempt_002独立冷启动复测。该中断不算作超时或完整300秒调用。
recovery_driver.py仅使用仓库现有atomic_write重试处理状态输出；原driver.py、manifest和全部求解源码／输入哈希保持。

| 算例 | 状态 | 可行 | 最优证明 | 目标 | 下界 | GAP(%) | 调用秒 | 建模秒 | K | 箱池 |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| S01 | TIME_LIMIT | False | False | — | — | — | 301.2424 | 126.9923 | None | — |
| S02 | TIME_LIMIT | False | False | — | 24.6311 | — | 301.5002 | 34.8480 | None | — |
| S03 | TIME_LIMIT | False | False | — | 22.7197 | — | 301.7071 | 39.0629 | None | — |
| S04 | TIME_LIMIT | False | False | — | 20.5001 | — | 300.6413 | 43.0282 | None | — |
| S05 | TIME_LIMIT | False | False | — | 25.6599 | — | 300.4405 | 17.4697 | None | — |
| S06 | TIME_LIMIT | False | False | — | — | — | 303.6889 | 105.7635 | None | — |
| S07 | TIME_LIMIT | False | False | — | 23.9460 | — | 301.3578 | 41.9171 | None | — |
| S08 | TIME_LIMIT | False | False | — | — | — | 304.7505 | 151.0784 | None | — |
| S09 | TIME_LIMIT | False | False | — | 21.9288 | — | 303.2287 | 42.3846 | None | — |

限时无可行解不等于不可行。完整结果、Gurobi日志、独立成本和两周期重放审计见runs/。
本报告只统计上述9例M8求解，没有运行全套单元测试。
