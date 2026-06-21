2026年6月7日 — 项目现状

## 项目概述
两级取送货车辆路径问题（2E-VRPTW-PDD）求解框架，包含五个递进模型 + Gurobi精确求解 + ALNS启发式求解。

## 代码结构

```
2E-VRPTW-PDD/
├── main.py                    # 入口：读取配置 → 构建实例 → 调用求解器 → 绘图
├── config/
│   ├── app_config.json        # 运行参数：模型选择、数据文件、求解器、ALNS迭代数/时限/种子
│   └── fleet_config.json      # 车队参数：E1/E2车辆数、载重/体积上限、容器参数、行驶速度
├── core/
│   ├── node.py                # 节点类：Node / Depot / Satellite / Customer / Task
│   ├── vehicle.py             # FleetConfig：车队参数管理（speed 可配置）
│   └── instance.py            # ProblemInstance：节点管理、需求聚合、距离/时间矩阵
├── models/
│   ├── alns_base.py           # ALNS 公共组件：Route、Solution、_roulette、clean_empty_routes、FEAS_TOL
│   ├── solver_utils.py        # 精确求解器公共工具：路由提取、载量打印
│   ├── model_1/               # 纯配送模型（MIP + ALNS）
│   │   ├── model.py           # Gurobi 模型构建
│   │   ├── constraints.py     # 约束定义（使用 time_map 而非 distance）
│   │   ├── objective.py       # 目标函数
│   │   ├── solver.py          # 精确求解入口（IIS 输出到 output/）
│   │   └── alns_solver.py     # ALNS 求解器（使用 alns_base 公共组件）
│   ├── model_2/               # 取送货模型（MIP + ALNS）
│   ├── model_3/               # 取送货 + 迭代中转模型（MIP + ALNS 含 transshipment_destroy）
│   ├── model_4/               # 取送货 + 两阶段中转模型（ALNS 后处理）
│   ├── model_5/               # 每轮 ALNS candidate 内嵌受控中转试探
│   └── strong_initial_solution.py # Model 5 强可行初始解构造
├── utils/
│   ├── data_loader.py         # CSV 数据解析
│   ├── task_builder.py        # 统一任务构建（根据 model 类型生成 E1/E2 tasks）
│   └── plot.py                # 路径可视化
├── experiments/               # 实验脚本与批量对比
├── docs/                      # 实验总结文档
├── data/                      # 测试数据（5/10/50/100/customers + small + large）
└── output/                    # 求解结果、IIS 文件输出目录
```

## 模型

### Model 1：纯配送 2E-VRP
- 只考虑配送（delivery），无取货任务
- E1：仓库 → 卫星站配送
- E2：卫星站 → 客户配送
- 求解方式：Gurobi MIP + ALNS

### Model 2：取送货 2E-VRP-PDP
- 在 Model 1 基础上增加取货（pickup）任务
- E1：先送后取（禁止同卫星 p→d 弧）
- E2：客户拆为 delivery + pickup 两个任务节点，送货减载、取货增载
- 求解方式：Gurobi MIP + ALNS

### Model 3：取送货 + 中转（迭代中搜索）
- 在 Model 2 基础上增加中转机制
- 中转定义：改变 pickup 节点的物理取货位置。顾客 A 的空容器可以"搬"到另一个顾客 B 的位置去取，车直接在 B 处取，从而减少总行驶距离
- pickup 节点留在原来的路线上，只改变其物理位置（task_to_phys 映射）
- ALNS 中：中转在迭代过程中搜索，配合 transshipment_destroy 算子撤销，与路线优化协同进行
- Model 3 ALNS 的中转候选策略可配置：
  - nearest_k：每个 pickup 只看最近 k 个候选点，便于和精确求解做同范围对比
  - all_sampled：候选点来自同卫星全客户点，但每轮抽样，默认模式，适合大规模
  - all_exhaustive：同卫星全客户点尽量枚举，适合小规模测试潜力上限
- 精确解入口：使用候选剪枝联合 MIP，同时决定 E2 路线、pickup 物理位置和搬运取放节点；默认每个 pickup 只考虑原位置 + 最近 1 个中转候选，主要适合 5/10 客户验证
- 求解方式：Gurobi MIP + ALNS（含 transshipment_destroy 算子）

### Model 4：取送货 + 中转（两阶段：先规划再中转）
- 阶段一：运行 Model 2 的 ALNS（无中转算子），收敛到最优路线
- 阶段二：对阶段一的固定路线，后处理搜索中转机会（改变 pickup 物理位置）
- 中转定义与 Model 3 完全一致：改变 pickup 的取货位置
- 结果保证：≤ Model 2 结果（只改进不恶化）
- 求解方式：exact 为 Model 2 MIP + 固定路线中转选择 MIP；ALNS 为 Model 2 ALNS + 后处理中转

### Model 5：取送货 + 每轮受控中转试探
- 在 Model 4 的“中转作为局部改进”思想基础上，把中转提前到每个 ALNS candidate 内部
- 每轮 ALNS 生成 candidate 后，执行最多 H 次中转试探；中转后的完整 candidate 再交给 ALNS 接受准则
- 当前主配置：H=1，稀疏中转上限 T1/S1 为主，T5/S3 作为补充
- 当前增强：
  - strong initial：先构造 E1 卫星配送，再构造 E2，再插入 E1 回收，提高大规模 R/RC 可行性
  - compact portfolio：多种中转策略与上限组合取最优
  - legacy safety：保留旧初始解 + baseline guard 分支，防止小规模算例变差
  - V3/V4：中转后的同卫星跨车重排与任务对交换
  - V5：把跨车重排/交换嵌入 ALNS candidate 过程
  - V6：Full 稳定增强 portfolio，所有规模均运行 4 个 V6 extra 分支
- 当前推荐版本：Model 5-Hybrid-Safety-Optimized-V6

### Model 3 vs Model 4 对比
- Model 3：中转在 ALNS 迭代中搜索，路线和中转协同优化，结果可能更好
- Model 4：先跑好路线再找中转机会，结果有下界保证（≤ Model 2）
- 两者中转机制完全一致：都是改变 pickup 的物理取货位置

### 当前 Model 5 实验结论

完整总结见：

```
docs/model5_optimization_summary.md
```

最新完整公平对比：全部非北京 120 个算例（5/10/50/100_customers，不含 beijing_fresh），
`iterations=60, time=6 per solver/variant, seed=42, transshipment_time_budget=0.02`，
Model 2/3/4 同样启用 strong initial，Model 5 使用 Full V6 portfolio。

| 模型 | 可行数 | 失败数 | 比强 Model 2 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|
| Model 2 + strong initial | 118 | 2 | 0 | 118 | 0 | 0.00% |
| Model 3 + strong initial | 118 | 2 | 35 | 49 | 34 | 0.42% |
| Model 4 + strong initial | 118 | 2 | 18 | 100 | 0 | -0.03% |
| Model 5-Hybrid-Safety-Optimized-V6 | 118 | 2 | 92 | 26 | 0 | -3.91% |

完整输出：

```
output/experiments/model2345_all_nonbeijing_120datasets_optimized_v6_iter60_seed42_strong_baselines.md
output/experiments/model2345_all_nonbeijing_120datasets_optimized_v6_iter60_seed42_strong_baselines.csv
```

全部非北京 120 个算例（5/10/50/100_customers，不含 beijing_fresh），
`iterations=80, time=10 per solver/variant, seed=42` 下，当前 Model 5-Hybrid-Safety 实测结果：

说明：Model 5-Hybrid-Safety 是多个候选分支取最优，计算量高于单次 Model 2 ALNS；这里主要比较解质量。

| 模型 | 可行数 | 失败数 | 比 Model 2 更好 | 持平 | 更差 | M2无解但可行 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|---:|
| Model 2 | 102 | 18 | 0 | 102 | 0 | 0 | 0.00% |
| Model 3 | 102 | 18 | 17 | 22 | 63 | 0 | 3.54% |
| Model 4 | 102 | 18 | 22 | 80 | 0 | 0 | -0.04% |
| Model 5-Hybrid-Safety | 118 | 2 | 84 | 18 | 0 | 16 | -7.84% |

作为对照，早期 strong compact Model 5 是 `78 更好 / 12 持平 / 12 更差 / 平均 -6.96%`。加入 legacy safety 分支后，12 个更差算例被修复为 `6 更好 / 6 持平 / 0 更差`。

完整输出：

```
output/experiments/model2345_all_nonbeijing_120datasets_hybrid_safety_iter80_seed42.md
output/experiments/model2345_all_nonbeijing_120datasets_hybrid_safety_iter80_seed42.csv
```

结论：Model 5 当前最优方向不是增加中转次数，而是 **强可行初始解 + 稀疏中转上限 + 多策略 portfolio + legacy safety 兜底 + 中转后的路线再均衡**。

进一步公平对比：让 Model 2/3/4 同样使用 strong initial 后，120 个非北京算例结果为：

| 模型 | 可行数 | 失败数 | 比强 Model 2 更好 | 持平 | 更差 | M2无解但可行 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|---:|
| Model 2 + strong initial | 118 | 2 | 0 | 118 | 0 | 0 | 0.00% |
| Model 3 + strong initial | 118 | 2 | 23 | 47 | 48 | 0 | 0.82% |
| Model 4 + strong initial | 118 | 2 | 13 | 105 | 0 | 0 | -0.01% |
| Model 5-Hybrid-Safety | 118 | 2 | 82 | 36 | 0 | 0 | -2.53% |

说明：strong initial 本身显著增强了 Model 2，可行数从 102 提升到 118；但统一强初始解后，Model 5 仍然保持 82 更好、36 持平、0 更差，说明模型五的中转迭代与 portfolio 仍有独立贡献。

进一步优化到 Full V6 后，在 84 个代表样本（5/10 客户 60 个 + 50/100 客户 24 个）上：

| 对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Stable V6 vs Model 2 | 56 | 26 | 0 | 2 | -3.49% |
| Stable V6 vs Current M5 | 37 | 45 | 0 | 2 | -1.04% |
| Stable V6 vs Optimized V5 | 8 | 74 | 0 | 2 | -0.06% |

当前统一口径：只保留 Full V6，不再区分 Adaptive V6。小规模额外计算量可以接受，优先保证实验口径清晰一致。

Full V6 代表输出：

```
output/experiments/model5_v6_stable_comparison_60datasets_iter60_seed42_strong_baselines_small_5_10_v6.md
output/experiments/model5_v6_stable_comparison_24datasets_iter40_seed42_strong_baselines_large_sample_50_100_v6.md
output/experiments/model5_v6_stable_comparison_84datasets_seed42_combined.md
```

## 求解方式
- exact：Gurobi MIP，TimeLimit=300s，MIPGap=0
- ALNS：自适应大邻域搜索，破坏算子(random/worst/shaw) + 修复算子(greedy/regret)，模拟退火接受
- Model 3 ALNS 额外包含 transshipment_destroy 算子（撤销中转位置变更）

## 关键设计

### 速度与时间计算
- 行驶速度在 fleet_config.json 中配置（默认 speed=1.0，此时 travel_time == distance）
- ProblemInstance 预计算距离矩阵（dist）和时间矩阵（time = distance / speed）
- ALNS Evaluator 中：dist() 用于成本计算，travel() 用于时间窗可行性检查
- 修改 speed 后所有模型均能正确适应

### E1 取货时间
- satellite.e1_pickup_ready_time 默认 9999.0，确保 E1 取货在所有 E2 配送完成之后
- 这是现实约束的简化：避免追踪每个顾客返回的最晚时间

### 载量模型
- 统一 Q/V 变量（送则减、取则增），共享容量由变量上界自然保证
- 配送重量 = 物品重量 + 容器重量（每个顾客一个容器）
- 回收重量 = 容器重量

### 中转机制
- 通过修改 task_to_phys 映射实现，改变 pickup 节点的物理位置后重新检查可行性（载量 + 时间窗）
- ALNS 破坏力度 max_remove = max(2, min(n // 2, 20))

### 公共模块
- alns_base.py：Route / Solution 数据结构、_roulette 轮盘赌选择、clean_empty_routes、FEAS_TOL
- solver_utils.py：精确求解器的路由提取和载量打印工具
- task_builder.py：统一任务构建接口，各模型/测试脚本共用

## 配置说明

### app_config.json
| 参数 | 说明 | 默认值 |
|------|------|--------|
| model | 模型编号 (1/2/3/4/5) | 4 |
| data | 数据文件路径 | data/100_customers/C101.csv |
| solver | 求解器 (exact/alns) | alns |
| alns_iter | ALNS 最大迭代次数 | 5000 |
| alns_time | ALNS 最大运行时间(秒) | 120 |
| seed | 随机种子 | 42 |

### fleet_config.json
| 参数 | 说明 | 默认值 |
|------|------|--------|
| E1_vehicle_count | E1 车辆总数 | 20 |
| E1_Qk | E1 载重上限 | 1500.0 |
| E1_Ck | E1 体积上限 | 30.0 |
| vehicles_per_satellite | 每卫星站 E2 车辆数 | 6 |
| E2_Qv | E2 载重上限 | 300.0 |
| E2_Cv | E2 体积上限 | 5.0 |
| container_weight | 容器重量 | 20.0 |
| container_volume | 容器体积 | 1.0 |
| speed | 行驶速度 | 1.0 |

## 运行
修改 config/app_config.json 中的 model 字段（1/2/3/4/5）和 solver 字段（exact/alns），然后：
```
python main.py
```

全部非北京算例对比：

```
python experiments/run_model2345_all_nonbeijing.py --iterations 80 --time 10 --transshipment-time-budget 0.03
```

Model 5 hybrid safety 针对性验证：

```
python experiments/run_model5_large_portfolio.py \
  --datasets data/10_customers/C109.csv data/10_customers/C203.csv \
  --iterations 80 --time 10 --transshipment-time-budget 0.03
```

Model 5 Full V6 优化分支对比：

```
python experiments/run_model5_optimized_comparison.py \
  --datasets data/5_customers data/10_customers \
  --iterations 60 --time 6 --transshipment-time-budget 0.02 \
  --strong-initial-baselines --tag small_5_10_v6
```

Model 3 ALNS 候选策略实验示例：
```
python experiments/test_model3.py data/5_customers/C101.csv -s alns --trans-mode nearest_k --max-reloc 1
python experiments/test_model3.py data/5_customers/C101.csv -s alns --trans-mode all_exhaustive --trans-budget 10
```

---
历史记录
---
2025年11月4日
  关于如何分配顾客给卫星站还需要一个方法
2025年11月6日
  思考一二阶段衔接问题
2026年3月8日
  代码结构重构，构建三个GRB求解模型
2026年5月17日
  新增 Model 4（两阶段求解），Model 3/4 中转机制统一为"改变 pickup 物理位置"
2026年5月18日
  全面代码审查与重构：抽取 ALNS 公共基类 (alns_base.py) 和精确求解器工具 (solver_utils.py)；
  统一任务构建 (task_builder.py)；修复距离/时间分离、E1取货时间窗逻辑、verbose 控制等关键问题；
  清理死代码 (core/route.py, utils/distance.py, Vehicle 类, TransshipmentTask 类)；
  速度参数可配置化 (fleet_config.json)
2026年6月7日
  新增 Model 5-Hybrid-Safety：每轮 ALNS candidate 内嵌受控中转试探；
  加入 strong initial 强可行初始解与 legacy safety 兜底分支；
  完成 120 个非北京算例对比，Model 5-Hybrid-Safety 实测实现 84 更好、18 持平、0 更差、16 个恢复可行，平均 gap=-7.84%。
