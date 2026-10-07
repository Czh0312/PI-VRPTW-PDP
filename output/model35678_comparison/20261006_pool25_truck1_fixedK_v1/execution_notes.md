## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run (continuous)
- Origin Date: 2026-10-06
- Verification Status: VERIFIED
- Version Label: pool25_truck1_fixedK_v1

用户授权一级里程费2→1重新实验。箱池25、磨损0.5、追踪0.1、清洗0.2/实际回库、二级0.056470588均保持。物理输入、308份算法源码、seed42、K1/2/3各300秒和3个worker与上一轮相同，无热启动；记录15秒心跳和480秒硬超时。历史18条已审计路线按新价重算并保留。

执行：`PYTHONPYCACHEPREFIX=/private/tmp/fixedk1_pycache MPLCONFIGDIR=/private/tmp/pool25_mpl OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python output/model35678_comparison/20261006_pool25_truck1_fixedK_v1/run_study.py`。9/9求解完成，9/9路线、成本、完整固定K预算、连续双周期审计及冻结哈希检查通过；仅一级距离系数2→1，308份算法和物理输入不变。6条真实多批均减箱，4条费用胜出本轮同模型K1；每个模型最低本轮K都是2。最低M8K2=2016.5359、N54，较本轮K1省264.7113（11.60%），较历史所有实际单批同价最好2269.9490省253.4131（11.16%）。9条新路线及18条历史路线的直接费用公式均复核一致，历史路线保留并单列新价。PNG/SVG生成并目视检查通过。辅助报告插入一度因查找标记错误失败，修正后复核通过，没有求解崩溃或自动重跑。
