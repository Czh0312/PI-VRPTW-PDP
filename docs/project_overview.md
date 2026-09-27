# 项目总览

## 1. 项目边界

项目包含两个独立但可组合的求解层：

1. 路径层求解两级配送、延后回收、自由分仓和空箱交接问题，输出路线、距离、时间与机制事件；
2. 成本层读取路径结果和成本参数，比较一次性包装与可循环容器方案。

成本层不直接绑定某个路径模型。只要输入指标满足统一口径，就能单独计算生命周期成本。

## 2. 路径模型

当前维护 Model 1–8：

| 模型 | 分仓 | 任务 | 空箱交接 |
| --- | --- | --- | --- |
| M1 | 固定 | 纯配送 | 无 |
| M2 | 自由 | 纯配送 | 无 |
| M3 | 固定 | 配送与延后回收 | 无 |
| M4 | 自由 | 配送与延后回收 | 无 |
| M5 | 固定 | 配送与延后回收 | 单次车辆间接力 |
| M6 | 固定 | 配送与延后回收 | 同质空箱异步交接 |
| M7 | 自由 | 配送与延后回收 | 同质空箱异步交接 |
| M8 | 固定分仓，路线自由优化 | 配送与延后回收 | 回收重分配与容量释放交接联合优化 |

M8 为单个联合优化问题，E1/E2 路线、配送回收与交接一起优化，无 M3 第一阶段。
当前规则下精确可行域与 M6 相同；ALNS 使用容量协作联合邻域，无需 MIP。详见 [M8 数学定义与求解](model8_capacity_cooperation.md)。

Model 1–7 的变体目录均保留当前完整模型和对应精确入口。Model 7 使用完整分仓分区与 Model 6 联合 MILP 构成严格精确求解；模型专属 ALNS 与数学模型分开存放，历史精确算法尝试位于 `archive/models/routing/variants/`。

## 3. 成本模型

`models/cost/` 计算包装采购、清洗分拣、追踪、损耗补充、报废处理以及 E1/E2 运输成本。参数按理论验证和真实案例分开配置：

- `config/cost/cases/theoretical_validation.json`：论文理论与敏感性分析；
- `config/cost/cases/lade_hangzhou.json`：LaDe-D 杭州真实道路案例。

参数出处位于 `reference/cost/`，模型定义见 [经济成本模型](economic_cost_model.md)。

## 4. 数据

当前只使用两类数据：

- Zhou later-pickup：用于小规模精确验证和中大规模 ALNS 对比；
- LaDe-D 杭州案例：用于带 OSRM 公里/分钟矩阵的真实道路成本分析。

旧 Solomon 派生数据、北京场景和随机机制算例不属于当前数据主线，其历史材料仅保存在 `archive/`。

## 5. 入口与输出

- `python main.py route ...`：运行单个路径案例；
- `python main.py cost ...`：运行成本分析；
- `python main.py compare ...`：运行 Zhou ALNS 批量比较；
- `experiments/runs/`：更细的精确、ALNS 和机制验证实验；
- `output/`：当前数据对应的报告与机器可读结果。

案例优先通过 `config/cases/*.json` 组合数据、车辆、算法和成本参数，避免在入口里重复硬编码路径。

## 6. 验证原则

- 精确结果必须检查 `proven_optimal`，不能把限时 incumbent 或受限列池最优写成全局最优；
- ALNS 比较采用相同时间预算、相同数据和相同随机种子集合；
- Zhou 坐标结果用于算法相对比较，不直接解释为真实运输金额；
- 真实金额结论使用 LaDe-D 的 OSRM 公里矩阵；
- 当前自动化测试只保留关键功能回归，不把目录结构检查当成业务测试。

## 7. 文档分工

- [实验路线](experiment_roadmap.md)：当前验证矩阵和报告口径；
- [精确求解架构](exact_solver_architecture.md)：精确入口和结果契约；
- [Model 2](model2_free_assignment_pure_delivery.md) / [Model 4](model4_free_assignment.md)：自由分仓模型；
- [Model 5](model5_exact_container_relay.md) / [Model 6](model6_compact_homogeneous_relay.md)：空箱交接模型；
- [Model 7](model7_hybrid_free_assignment_relay.md)：自由分仓与异步交接联合模型及 ALNS；
- [真实案例成本分析](real_case_cost_analysis.md)：LaDe-D 案例口径与结果；
- `archive/docs/`：旧设计、迁移记录和历史实验说明。
