## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run (continuous)
- Origin Date: 2026-10-06
- Verification Status: VERIFIED
- Version Label: pool25_truck2_fixedK_v1

用户授权：箱价25，里程系数2。结合此前一级车2.535的讨论，将里程2应用于一级车；二级三轮车0.056470588保持。磨损随箱价自动变为25/50=0.5，追踪0.1，清洗按回库0.2。输入、算法、seed42、固定K1/2/3各300秒和3进程与此前固定K诊断相同。无热启动。单例单种子不证明全局最优。每个worker监测心跳，480秒硬超时。

执行：`PYTHONPYCACHEPREFIX=/private/tmp/fixedk25_pycache MPLCONFIGDIR=/private/tmp/pool25_mpl OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python output/model35678_comparison/20261006_pool25_truck2_fixedK_v1/run_study.py`。9/9求解完成，9/9独立路线和双周期审计、固定K完整预算、费用公式及冻结哈希检查通过。308份算法文件保持。5条真实多批均减少箱池，0条胜出同模型本轮K1。M7K2省箱650、新增运输804.42、净增154.42；与旧单批按新价重计2508.88比较则贵259.03。旧保存路线同价对照中3条新搜索更低、6条更高，保留全部旧可行方案。PNG/SVG生成并目视检查通过。Fontconfig沙箱缓存警告仅影响缓存写入，没有阻碍绘图。准备阶段纠正费用类导入名，求解前注册及检查通过；无求解失败、无自动重跑。
