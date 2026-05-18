2026年5月18日 — 项目现状

## 项目概述
两级取送货车辆路径问题（2E-VRPTW-PDD）求解框架，包含四个递进数学模型 + Gurobi精确求解 + ALNS启发式求解。

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
│   └── model_4/               # 取送货 + 两阶段中转模型（ALNS 后处理）
├── utils/
│   ├── data_loader.py         # CSV 数据解析
│   ├── task_builder.py        # 统一任务构建（根据 model 类型生成 E1/E2 tasks）
│   └── plot.py                # 路径可视化
├── experiments/               # 实验脚本（test_model1/2/3.py）
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
- 精确解（Gurobi MIP）：一次性建模，同时决定路线和中转变量
- 求解方式：Gurobi MIP + ALNS（含 transshipment_destroy 算子）

### Model 4：取送货 + 中转（两阶段：先规划再中转）
- 阶段一：运行 Model 2 的 ALNS（无中转算子），收敛到最优路线
- 阶段二：对阶段一的固定路线，后处理搜索中转机会（改变 pickup 物理位置）
- 中转定义与 Model 3 完全一致：改变 pickup 的取货位置
- 结果保证：≤ Model 2 结果（只改进不恶化）
- 求解方式：Model 2 ALNS + 后处理中转

### Model 3 vs Model 4 对比
- Model 3：中转在 ALNS 迭代中搜索，路线和中转协同优化，结果可能更好
- Model 4：先跑好路线再找中转机会，结果有下界保证（≤ Model 2）
- 两者中转机制完全一致：都是改变 pickup 的物理取货位置

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
| model | 模型编号 (1/2/3/4) | 4 |
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
修改 config/app_config.json 中的 model 字段（1/2/3/4）和 solver 字段（exact/alns），然后：
```
python main.py
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
