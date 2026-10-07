# 箱价15固定K诊断执行记录

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run (continuous)
- Origin Date: 2026-10-06
- Verification Status: VERIFIED
- Version Label: pool15_fixedK_diagnostic_v1

选择100-10-1的50点算例，Q=78。预先固定M6/M7/M8各K=1/2/3，每次使用同一费用目标、完整300秒、迭代上限1000000、seed42。上一轮每个模型总计300秒分配给三个K；本轮每个K不再共享预算。3个子进程并行，9次运行，预先打乱顺序。不以结果好坏选择算例、K或模型。

参数为箱池15N、一级2.535D1、二级0.056470588D2、追踪0.1U、磨损0.3U、清洗0.2R，工时/车辆/趟次固定费均0。原距离、速度、时间窗、周期、容量和订单保持不变。原速度2单位/分钟；不能据此声称真实深圳城市配送可行。

控制K指日历段数，原求解器允许空段。本轮不新增非空批次约束、不强制复用、也不更改模型算法。报告同时记录实际配送批次与箱池N，不能将K>1但只发一次货解释为多批收益。K改变本身必然改变日历可行域，不是同一个可行域中的最优证明。

预检九个任务的batch_count_candidates均为单元素[K]；经济参数语义哈希与上一轮相同；物理文件字节相同；308份求解源码哈希与上一轮一致。没有热启动已有方案，各K均从本来的初始化过程搜索。Python3.9.6，与上一轮箱价15相同；worker由4改为3，共享机器负载和墙钟截止仍影响轨迹。

执行命令（注册后）：

```sh
PYTHONPYCACHEPREFIX=/private/tmp/fixedk15_pycache MPLCONFIGDIR=/private/tmp/pool15_mpl OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -u -c "import runpy; r=runpy.run_path('output/model35678_comparison/20261006_pool15_fixedK_diagnostic_v1/run_study.py'); r['run']()"
```

每个进程300秒软预算、480秒硬超时；15秒heartbeat；结果保存后做独立物理审计、双周期复用、费用重算与固定K核对。失败保留，不把无解成本记零。与上轮同K候选的对照只引用内部审计摘要，原候选路线未逐一保存，不称其为独立审计结果。

执行完成：9/9成功；固定K和每K约300秒分配核对通过；独立路线重放和连续双周期核对通过；9项总费用按原始审计量独立核算通过；冻结字节及308份算法源码哈希通过。4条真实多批路线均减少箱池，净费用胜出0条。M8的K2/K3均实际只配送一次。全部九个新同K候选优于先前约100秒摘要候选。

求解无崩溃或硬超时。辅助进度页初版缺少列表右括号，修正后正常更新，求解过程持续运行。结果图已检查。
