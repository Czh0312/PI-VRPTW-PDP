# 直接 Gurobi 求解与实验口径

2026-10-19 起，M1–M8 的 `solve_exact` 默认使用 `strategy='milp'`，`auto` 是同一路径的兼容别名。
论文主线为完整 MILP + Gurobi，与 ALNS 对比；不再将定制精确算法作为必要贡献。

## 默认求解范围

| 模型 | 直接 Gurobi 的完整问题 |
| --- | --- |
| M1/M3 | 固定分仓、单批次 MILP |
| M2/M4 | 分仓与路线同时决策的联合 MILP，无外层分仓域树 |
| M5 | 固定批次日历的完整多批次 MILP |
| M6 | uniform 模式在一个 MILP 内选择 K、共同截止、订单分批、箱池和路线；历史固定日历输入仍为一个 MILP |
| M7 | 一个 MILP 内选择 K、等长批次的各独立截止、订单分批、箱池和路线 |
| M8 | 一个 MILP 内选择 K、批次边界、各截止、订单分批、箱池和路线 |

M6–M8 使用二元批次数选择变量和条件业务块，一次 `optimize()` 求解整个问题。
各 k 的业务块只构造、不单独求解。完整数学表达、等价性证明、整数时钟及规模限制见
[统一批次数 MILP](unified_batch_count_milp.md)。

## 配置

除下述 M3 输入型加强外，默认求解入口不构造/接受 MIP Start 或变量提示。
各模型不调用自定义回调、外部距离/库存/路线证书，
不调用自适应分仓搜索或 K 调度。M6–M8 额外的必要容器池/总成本强化行已移除。
保留数学模型的有效变量界、合法弧、车辆编号约定及必要的因果、容量、同步与库存约束。
这些是声明的模型表达；不能把业务必需约束当成加速项删除。

Gurobi 内置 Presolve、Cuts、Heuristics、MIPFocus 保持默认；默认单线程，
`MIPGap=0`、`MIPGapAbs=0`，受总时间预算限制。浮点求解及最优性以求解器容差和独立审计为准。
M3、M4默认使用等价的共享内部弧`compact`表达；其余模型默认`expanded`，显式表达请求均保留。

M3对所有规模默认加入逐趟载荷启用联系、容量推导的必要趟数，并按仓组合LP/解析下界与
已完成枚举的整数划分下界。未完成车次库只作初解候选，不产生下界；完成的仓不因其他仓超时而丢弃。
成本模式还从当前输入新生成完整调度内部初解。完整主模型仍求解所有原始物理选择，
原生状态与独立审计是最终证明依据。两个准备阶段各占用至多剩余预算的25%且不超过5秒，
不需要按订单规模更换参数。
`solve_exact(..., route_strengthening=False)` 恢复原冷启动基线，详情见
[M3 成本对照与加速](model3_cost_comparison.md)。历史冻结实验不自动重标为新版。

M1–M4 的初解效果另通过[热启动独立试验](single_batch_warm_start_trial.md)评估。
该入口显式接入已审计方案并记录观察轨迹，不改变上述默认入口或冻结实验口径。
M4 另提供显式 `--m4-strengthening` 选择[容量与连通性表达强化](model4_strengthening.md)，默认 `none`。
这些是对原合法路线成立的静态约束，不使用外部下界、初解或自定义割回调；须按声明的表达分别比较性能。
M4另默认启用自由选仓的配送/回收弧流、因果冲突和与趟数排序兼容的车辆对称约束，
不移用M3固定分仓的路线下界。Python入口 `network_strengthening=False` 可恢复原默认基线，
见[M4网络表达加速](model4_network_acceleration.md)。

```python
from models.routing.service import solve_routing

result = solve_routing(model=8, method='exact', problem=problem,
                       exact_time=300, seed=42)
# 可选 milp_options={'threads': 1, 'formulation': 'expanded'}
```

```bash
.venv/bin/python main.py route --case CASE.json --solver exact --exact-time 300 --seed 42 --output NEW_OUTPUT
```

模型建造接口 `exact/model.py:build_model` 对 M6–M8 的可变批次数输入默认返回完整统一模型。
显式 `batch_count=k` 返回固定 k 诊断模型，其最优性不能冒充完整候选域的最优性。

## 结果与时间

- M1–M5 和固定日历 M6：`info.exact_strategy='gurobi_milp'`。
- 可变批次数 M6–M8：`info.exact_strategy='gurobi_unified_milp'`。
- `info.batch_count_candidates` 保存完整输入 K 域；`complete_count_domain` 表示是否完成全部建模。
- `cycle_batch_count` 是选中的 K；无可行解时为空。
- `best_bound`/`gurobi_best_bound` 来自同一个完整模型，`proof_scope` 为完整问题范围。
- 统一求解不伪造逐 K 的状态、访问次数或最优性，不再输出 `count_search`/`count_search_visits`。
- `variables`、`constraints`、`build_time`、`mip_runtime` 描述整个模型；总墙钟时间另含提取与审计。
- M3 的 `preparation_time` 包含完整建模、输入型下界与内部初解准备；后两阶段分别在
  `route_strengthening` 与 `internal_start` 记录，均从总优化预算扣除，不算进主模型的原生 `mip_runtime`。

建模超时不优化残缺 K 域；限时无可行解不等于不可行。完整 Gurobi 最优状态且独立审计通过才发布
`proven_optimal=true`。ALNS 继续报告其自身搜索记录；与 Gurobi 使用相同业务域和成本口径。
正式驱动只记录统一求解调用结束时确认的解，不把它冒充首次发现可行解的精确时刻。

## 已退役研究代码

旧增强方法、分仓域树、列生成、逻辑 Benders、路线池和混合接口已从当前代码移除。
CLI 只保留 `milp` 与同义的 `auto`；旧增强选项与外部初解参数会被拒绝。
清理前源码及专用测试/驱动见[研究归档](../archive/exact_research_20261019/README.md)。
`--baseline` 仍只指早期旧业务规则的历史模型，与当前直接 Gurobi 无关。
已有冻结实验和 output 结果保持原样；新实验使用新输出目录，不混合新旧求解版本。
