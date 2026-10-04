# M5：精确求解

`solver.py` 是本模型的独立入口，调用 `../../../solvers/exact/` 下的共用实现。
依赖 Gurobi；最优性和不可行证明以结果字段及审计为准。

默认启用独立多批次初解、二级任务状态共享及安全时间界。
初解只作为经独立审计的可行上界；限时或构造失败不改变完整模型的证明口径。
实现与验证见 [M5 优化报告](../../../../../docs/m5_exact_optimization_20260930.md)。

第三步默认启用订单级截止状态、容量关联和按需容量割，见
[后续强化与多种子对照](../../../../../docs/m5_exact_strengthening_20260930.md)。

第四步扩展二层配送/回收插入位置，并在完整周期审计下合并一层路线，改善可行上界。
见 [初解质量与同预算对照](../../../../../docs/m5_exact_primal_quality_20261001.md)。

第五步把二层条件关系按有效上下界直接线性化，并按同质车辆使用趟数压缩趟位。
见 [预处理与根节点优化](../../../../../docs/m5_exact_root_20261001.md)。

第六步在较大模型中共享二层内部弧，以任务所属趟次重建路线；小模型保留原表达。
见 [内部弧压缩与同预算验证](../../../../../docs/m5_exact_routes_20261001.md)。

第七步增加独立的完整周期距离下界，根节点未完成时也可报告已证明有效的界。
Gurobi 原始界和最优性标志分别保留；见 [距离下界与根节点诊断](../../../../../docs/m5_exact_bound_certificate_20261001.md)。

第八步可用 `solve_exact(..., distance_cuts='components')` 将证书按一层批次、二层仓加入有效距离约束；
`'total'` 只添加总距离行，默认仍仅报告证书。见
[距离证书约束与同初解对照](../../../../../docs/m5_exact_certificate_cuts_20261001.md)。

第九步增加显式 `route_cover_certificate(problem, time_limit=60, initial_result=audited_result)`
入口，通过二层路线覆盖列生成计算完整 M5 的松弛下界。定价限时使用经修正的对偶证书，
不会把未收敛受限主问题目标作为下界；LP 完成不表示整数最优，也不改变现有求解器默认路径。
见 [列生成原型与同预算结果](../../../../../docs/m5_route_cg_20261001.md)。

第十步可显式调用 `route_cover_certificate(..., e1_time_limit=10)`，在同一总预算内计算
一级整路线、回收截止数量及清洗库存松弛下界，并替换较弱的一级距离界。
最多四个实际服务仓时完整枚举一级路线，更多仓使用一级弧模型。
`e1_inventory_certificate` 可单独计算一级成本界；两种入口都不产生完整 M5 最优性证明。
见 [一级路线与清洗库存下界](../../../../../docs/m5_e1_inventory_20261001.md)。

第十一步提供 `reoptimize_e1(problem, audited_result, retime_e2=True)`，保留二层访问顺序、
车辆与趟位，联合调整一级路线和二层实际日程，只接受完整周期审计合格的可行上界。
完整入口可用 `solve_exact(..., initial_result=audited_result, e1_polish_time=5)` 在原总预算内
生成更好的热启动；固定二层只限制该改进步骤，不限制后续完整 MILP。
受限子问题最优不表示整体最优，见 [一级与二层时刻联合改进](../../../../../docs/m5_e1_polish_20261001.md)。

第十二步提供 `reoptimize_route_pool(problem, audited_result, time_limit=10)`，联合选择二层
重排/跨趟转移候选和一级路线，保留实际物理车辆、服务/回仓时刻、回收截止及清洗库存。
`solve_exact(..., route_pool_time=10)` 可在原预算内生成热启动，默认仍为 0。
候选池界只描述受限主问题，不作为 M5 全局界，见 [二层路线池优化](../../../../../docs/m5_route_pool_20261001.md)。

第十三步可用 `route_cover_certificate(..., pricing_backend='hybrid', e1_time_limit=10)`，
在原总预算内完整枚举二层任务路径，保留每个任务集合的最低成本路线并求解完整覆盖 LP。
枚举受时间/状态上限保护，未完成时回退到原完整 MILP 定价；默认仍为 `'milp'`。
50 顾客本组多种子均得到约 2.94% 的上下界 gap，LP 收敛仍不证明完整整数最优。
见 [完整二层枚举与定价收敛](../../../../../docs/m5_enumerated_pricing_20261001.md)。

第十四步可在上述完整路线域证书中增加 `integer_cover_time_limit=15`，
在原总预算内求解完整二元路线覆盖，取更强的整数松弛下界；默认仍为 0。
只有完整枚举域可以进入整数证书，限时保留已验证的 LP 界。
本组 50 顾客 gap 进一步降到约 2.31%，完整 M5 最优性标志仍为 False。
见 [完整整数覆盖下界](../../../../../docs/m5_integer_cover_20261001.md)。

第十五步增加 `joint_route_inventory_certificate(problem, initial_result=audited_result, time_limit=60)`，
联合完整二层覆盖的整趟截止选择与一级回收、清洗库存，再恢复真实物理车辆日程。
完整列域才能进入联合下界；恢复只接受完整审计合格的可行上界，候选池局部界不作为全局界。
本组 50 顾客最好上界为 2247.72、gap 约 1.70%，主要来自二层路线改进，默认求解路径不变。
见 [联合截止库存与真实方案恢复](../../../../../docs/m5_joint_inventory_20261001.md)。

可显式调用 `solve_hybrid(problem, time_limit=660, seed=43)`，统一执行新 ALNS、
完整周期审计的路线池改进、带容量子集约束的完整域定价、条件供给联合下界及剩余预算补用。
需要 Gurobi；达标与否由 `target_reached` 报告，已有合格初解时，未达标仍返回审计可行解和有效下界。
路线池可用 `additional_columns={satellite: columns}` 加入定价提案，每条序列重新计算成本和必要时刻；
该候选池的最优性与界仍只属于受限子问题。默认 ALNS 入口不变。
见 [混合入口与跨实例 GAP 验证](../../../../../docs/model5_gap_round4_20261001.md)。
混合入口会按各仓未完成证明的差距分配余量，并在相同对偶价格下续算困难批次。
覆盖证明也可显式设置 `batch_pricing=True, reuse_pricing_budget=True`；完整路线域及成本不变。
见 [跨仓预算与定价余量验证](../../../../../docs/model5_gap_round5_20261001.md)。

第十六步可显式启用 `joint_route_inventory_certificate(..., timing_envelopes=True, time_limit=120)`，
完整保留路线成本与最晚出发时刻的权衡，按完整一级供货值压缩并添加不兼容选择连接。
小仓数路径使用连续时间列与完整整数覆盖成本界；联合混合松弛最优仍不代表完整 M5 最优。
本组同为 120 秒对照将 50 顾客下界提高到约 2235.41，gap 约 0.55%；默认关闭。
见 [供货时间与路线成本的联合下界](../../../../../docs/m5_route_timing_20261001.md)。
