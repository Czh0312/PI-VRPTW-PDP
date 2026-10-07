# 箱价15：实验执行记录

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run (continuous)
- Origin Date: 2026-10-06
- Verification Status: VERIFIED
- Version Label: truck_tricycle_pool15_5min_v1

用户要求把箱子成本从5改为15并重新运行。箱池费用15N；使用磨损15/50=0.3U；追踪0.1U；清洗0.2R。主公式为2.535D1+0.056470588D2+15N+0.4U+0.2R。备选清洗0.8只重计同一方案，不另做搜索。

沿用原六个算例与M3/M5/M6/M7/M8，每次ALNS 300秒、seed42、上限1000000次迭代、4个worker；M6–M8共享300秒于K=1/2/3。只改变经济配置中的container_pool_cost_per_period与container_price，源代码308份文件、物理输入字节和其他费用均冻结。没有修改道路距离、速度、时间窗或箱子复用机制。

距离仍是benchmark单位，原速度仍为2单位/分钟；如把距离当公里，对应120公里/小时。本轮用于与上一轮隔离箱价影响，不作为深圳实际运行时间的验证。没有按当前柴油价格更新2.535，也没有引入人工、维修或车辆固定费。

原runner来自远程提交b95c13f5c。为了适应本机，在新目录内修改了路径重定位、参数校验、费用报告和heartbeat实现；使用resource模块替代缺失的psutil。求解器算法源码未修改。运行Python3.9.6，与上一轮Windows Python3.11.3不同，且为按墙钟时间截止的启发式搜索，不能声称只由箱价导致搜索输出变化。

命令（注册完成后执行）：

```sh
PYTHONPYCACHEPREFIX=/private/tmp/pool15_pycache MPLCONFIGDIR=/private/tmp/pool15_mpl OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -u -c "import runpy; r=runpy.run_path('output/model35678_comparison/20261006_truck_tricycle_pool15_5min_v1/run_study.py'); r['run']()"
```

每个worker限制300秒软预算、480秒硬超时；实时状态为results/*/live_status.json；逐次进度为rows.json和progress.md；失败完整保留，不把无解成本当0。最终重放所有返回路线、校验费用与箱池、核对连续两个周期，并对上一轮保存路线按新价重计以分离参数变化与新搜索效果。

执行记录：预检参数仅两项变化通过；30个任务注册完成；30/30求解及双周期复核通过。

最终记录：30/30次求解成功；独立重放和双周期复用通过30次；60项主/备选总费用按原始审计量独立核算通过；冻结输入及308份算法源码哈希通过。18个联合模型方案均单批、N=Q；27个内部有效减箱候选仍高于各自K=1。

异常记录：求解过程无崩溃或硬超时。统一后处理首轮因旧结果任务编号字段名source_run_id不兼容而停止，修正为id后通过；没有重新运行求解器。绘图出现fontconfig缓存写权限提示，但PNG/SVG均生成，图中文字和数字经视觉检查可读。
