2026年5月9日 — 项目现状

## 项目概述
两级取送货车辆路径问题（2E-VRPTW-PDD）求解框架，包含三个递进数学模型 + Gurobi精确求解 + ALNS启发式求解。

## 模型
- Model 1：纯配送 2E-VRP（完整：exact + ALNS）
- Model 2：统一取送货 2E-VRP-PDP（完整：exact + ALNS，统一Q/V变量，送减取增）
- Model 3：带横向中转 2E-VRP-PDP-TR（完整：exact + ALNS，统一Q/V变量，中转Δq/Δw）

## 求解方式
- exact：Gurobi MIP，TimeLimit=300s，MIPGap=0
- ALNS：自适应大邻域搜索，破坏算子(random/worst/shaw) + 修复算子(greedy/regret)，模拟退火接受

## 关键设计
- E1取货时间窗下界 = satellite.e1_pickup_ready_time，确保先送后取
- 统一Q/V变量（送则减、取则增），共享容量由变量ub自然保证
- 中转节点T为E2客户任务节点子集，约束(19)对T节点放宽为1≤sum≤2
- ALNS破坏力度 max_remove = max(2, min(n // 2, 20))

## 运行
python main.py -m [1|2|3] -d data/small/test2.csv -v 2         # 精确求解
python main_with_alns.py                                        # JSON配置驱动

---
历史记录
---
2025年11月4日
  关于如何分配顾客给卫星站还需要一个方法
2025年11月6日
  思考一二阶段衔接问题
2026年3月8日
  代码结构重构，构建三个GRB求解模型
