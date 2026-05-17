2026年5月17日 — 项目现状

## 项目概述
两级取送货车辆路径问题（2E-VRPTW-PDD）求解框架，包含四个递进数学模型 + Gurobi精确求解 + ALNS启发式求解。

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
- 求解方式：Model 2 MIP + 后处理启发式 / Model 2 ALNS + 后处理中转

### Model 3 vs Model 4 对比
- Model 3：中转在 ALNS 迭代中搜索，路线和中转协同优化，结果可能更好（3211.95）
- Model 4：先跑好路线再找中转机会，结果有下界保证（≤ Model 2），结果（3258.60）
- 两者中转机制完全一致：都是改变 pickup 的物理取货位置

## 求解方式
- exact：Gurobi MIP，TimeLimit=300s，MIPGap=0
- ALNS：自适应大邻域搜索，破坏算子(random/worst/shaw) + 修复算子(greedy/regret)，模拟退火接受
- Model 3 ALNS 额外包含 transshipment_destroy 算子（撤销中转位置变更）

## 关键设计
- E1取货时间窗下界 = satellite.e1_pickup_ready_time，确保先送后取
- 统一Q/V变量（送则减、取则增），共享容量由变量ub自然保证
- 中转通过修改 task_to_phys 映射实现，change pickup 节点的物理位置后重新检查可行性（载量 + 时间窗）
- ALNS破坏力度 max_remove = max(2, min(n // 2, 20))

## 运行
修改 config/app_config.json 中的 model 字段（1/2/3/4）和 solver 字段（exact/alns），然后：
python main.py

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
