# 正式27例：一客一箱版本

输入版本为 `data/formal_benchmark27_one_box_v1`，含27个基础算例、216份八模型配置。由旧版修订3的已冻结正式输入派生；原 `formal_benchmark27_v1` 及所有冻结实验保留，统一视为“多箱需求情景”。两类情景的结论分别报告。

每位顾客恰好一个订单、一个标准箱，保留原始货物重量。假设每位顾客的需求可由一个标准箱承载，不进行装箱优化。配送载重为货物重量加一个箱重，回收载重为一个空箱重；两类任务体积均为一个箱体积。S01为7位顾客、Q=7。

派生程序从旧正式快照读取，保留27例的顾客、网络坐标、货重、送取窗、释放、服务及使用时间、分仓、原参考订单分批、车队、周期和费用系数。更新箱数、Q、M3–M5固定箱池、M6–M8参考箱池及优化上限、说明、来源记录和哈希。M1/M2不执行回收和清洗。M6–M8仍在1≤N≤Q内优化，不能把参考N=Q当作最优结论。

M5沿用三批及原70%参考分界；M6–M8沿用原非空配送和回库批次定义、最短阶段和完整数学域，仅保留原输入证明的K排除。逐例要求新旧批次数证书哈希一致。K≤3不属于本版正式数据。

费用仍为0.1D1+0.05D2+5N+0.02Q，单位箱次费系数不变，Q随新箱数变化；M1–M5仍按距离优化，M6–M8按成本优化。一次性包装的成本评价仍需要单独的包装单价。

## 可复现入口

生成器为 `tools/data/build_one_box_benchmark27.py`，选例冻结于 `config/experiments/benchmark27_one_box_v1/selection.json`；拒绝覆盖已有非空数据集。`tools/data/design_static_scenarios.py` 的Zhou生成规则同时改为一客一箱，当前生成器使用 `config/datasets/static_batch_one_box_v1.json`，移除需求量除以15的参数。旧配置 `static_batch_v2.json` 按原字节保留，用于历史来源哈希复核。Lade来源的原观测箱数继续保留，不并入本27例一客一箱范围。历史静态数据不重新生成或覆盖。

在已配置的项目Python运行环境执行：

```sh
python -m tools.data.build_one_box_benchmark27 --output data/formal_benchmark27_one_box_v1
python -m tools.audit.audit_formal_benchmark27 --dataset data/formal_benchmark27_one_box_v1
python -m tools.audit.certify_formal_benchmark27 --dataset data/formal_benchmark27_one_box_v1 --verify
```

本机运行遵循本地项目入口及Gurobi预检要求。输入审计加载全部216份配置，验证一客一箱、八模型物理输入、载重/体积、固定与优化决策及K域；可行性见证另由独立双周期重放检查配送、回收、清洗和库存守恒，且不会自动用作算法初始解。

## 阶段与实验门槛

首阶段：数据审计与相关回归通过，S01八模型精确求解和ALNS实际运行并审计；再覆盖S04（10顾客）、S07（12顾客）。以实际箱池、批次、路线、成本、时间和gap报告结果，超时未找到解不等于不可行，找到可行解也不等于证明最优。

小规模验证通过后才推进正式全量重跑。正式预算沿用精确300秒、ALNS30秒，ALNS种子42、43、44、45、46；完整基准为216次精确与1,080次ALNS。每个阶段prepare到独立新目录，记录输入/代码哈希及独立审计。状态与实际运行证据保存于 `output/inspections/one_box_validation_20261019_v1/`，未完成的阶段不能用于论文结论。
