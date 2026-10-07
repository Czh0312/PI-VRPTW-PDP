## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run (continuous)
- Origin Date: 2026-10-06
- Verification Status: VERIFIED
- Version Label: pool25_truck1_autoK_5min_v1

用户要求恢复此前正式实验，不提前固定K。六例×五模型=30次，每次总预算300秒、seed42、1000000迭代、4个worker。M6–M8一次调用候选K1/2/3由程序分配预算并返回最低有效成本；不是每个K300秒，不人为指定最终K。M3/M5沿用此前距离目标作为基准，M6–M8成本目标。主价格一级1、箱25、二级0.056470588、磨损0.5、追踪0.1、按回库清洗0.2。原物理输入和308份算法保持，无热启动。历史固定K结果另留诊断，不混入本轮正式结果。心跳15秒、硬超时480秒。

执行：`PYTHONPYCACHEPREFIX=/private/tmp/autoK1_pycache MPLCONFIGDIR=/private/tmp/pool25_mpl OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python output/model35678_comparison/20261006_pool25_truck1_autoK_5min_v1/run_study.py`。

30/30求解完成、独立路线和连续双周期复核全部通过；18/18联合调用确认K1/2/3候选、总预算300秒、最终目标等于最低有效候选。16个实际两批并减箱、2个单批（100-5-1b的50点M7/M8）；M6六例两批，M7/M8各五例两批、一例单批。两个M8返回K3但实际两批。308份算法、120对物理输入文件、6例、seed42及4个worker与原正式实验一致。无热启动、无求解失败、无自动重跑。30个主公式及30个清洗0.8同路线计价对照通过；后者未另搜索。旧正式路线同价比较23个更低、6个更高、1个相同。PNG/SVG生成并目视检查通过。

途中一次进度描述将单批归属误报为M6，已根据rows.json纠正为50点100-5-1b的M7；不影响实验数据。最后一个辅助保存检查记录的stdin命令编码报错，改为UTF-8源文件后保存成功，没有重新运行求解器或独立路线审计。
