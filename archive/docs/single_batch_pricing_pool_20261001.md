# 单批次 M1/M2：批量定价与根节点效率

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Mode: run; user-authorized solver implementation and frozen comparisons
- Verification Status: EXECUTED; independent pricing and assignment-partition oracles
- Scope: B=1 M1/M2; optional MILP pricing strategies, complete integer solver retained

## 改动

上一轮[分仓对偶约束](single_batch_dual_cuts_20261001.md)能强化完整 MILP 的下界，
但 30 客户时限定价完成的轮次不同，导致端到端效果不稳定。
原 MILP 定价每次最多返回一条负约化费用路线，其他已经发现的路线没有交给路线主问题。
本轮实现两种显式策略：

- `mip_strategy='pool'`：每次读取最多 16 个已发现解，验证并去重，批量加入有效的负费用路线。
- `mip_strategy='negative'`：同样允许批量读取，但发现足够负的可行解后可提前结束定价。

默认仍为 `mip_strategy='optimal'`，保留原来的单路线返回方式。
批量方式使用 Gurobi 默认的机会式解池搜索，不为寻找 16 个最好解另开穷举搜索。
每条路线均按原始有向距离、整数时间窗、载重/体积和回仓期限独立重放，
重新计算约化费用并核对解池目标，只加入费用严格为负的路线。
相同订单次序去重；不同订单在同一物理地址仍是不同任务。

初次专项测试发现，16 个解可能只是同一路线的不同时刻，导致只得到一条有效新列。
本轮为批量策略设置时刻与辅助次序变量的 `PoolIgnore`，只让路线结构区分候选，
每一时间/次序约束均继续保留。单路线默认策略将该属性恢复为 0。
该属性控制解池的差异判定，可查 [Gurobi 12.0 变量属性说明](https://docs.gurobi.com/projects/optimizer/en/12.0/reference/attributes/variable.html#poolignore)。
第一次测试的失败信息保存在 `initial_test_failure.txt`；修正后重新验证，没有删除失败记录。

提前停止使用尺度相关阈值 `-max(1e-5, 10*margin)`，防止微小数值负值触发。
每次重新定价时恢复停止参数、更新全部目标系数，并将解池读取编号恢复为 0。
有关提前终止语义，见 [Gurobi BestObjStop 说明](https://docs.gurobi.com/projects/optimizer/en/current/reference/parameters.html#bestobjstop)。

## 证明边界

批量路线只是列发现工具，解池不完整不影响主问题的模型范围，也不能证明不存在负费用路线。
下界仍取完整定价问题的 `ObjBound` 与解析下界的保守最大值，扣除数值余量，
再通过每仓趟数和任务数修正主问题对偶值。没有使用解池候选费用或 `PoolObjBound` 作为完整定价下界。

提前达到目标阈值返回 `USER_OBJ_LIMIT` 时，定价的 `complete` 为 False。
只有 `OPTIMAL` / `INFEASIBLE` 才满足定价完成判定；根节点还需所有仓点完成且无负费用路线才能关闭。
阶段一的不可行性证明、阶段二的下界、分仓对偶不等式和完整 MILP 的最优性均继续沿用原有证明条件。
没有新增筛掉可行路线的限制。M3/M4 明确不进入该纯配送原型。

## 冻结探索

结果目录 `output/experiments/single_batch_pricing_20261001/`。
`root_pilot/` 在 30 客户 M2、seed 42、30 秒根节点上限下顺序比较三种方式。
相同输入、已审计初解、单线程配置和冻结源码；没有整数求解，没有并行求解测试，也没有失败重试。

| 根节点策略 | 耗时（秒） | LP 迭代 | 定价调用 | Gurobi Work | 路线数 | 可用下界 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 原单路线 | 29.372 | 72 | 129 | 45.738 | 400 | 609.455567 |
| 批量路线 | 13.412 | 44 | 57 | 20.640 | 470 | 609.455484 |
| 负费用提前停止 | 24.971 | 131 | 273 | 37.217 | 540 | 609.455639 |

三者的路线松弛最优值均为 609.473821542597（数值容差内相同）。
证书下界的小差异来自不同对偶尺度的数值余量，不是实质性的松弛强化。
批量方式有 47 次定价返回超过一条负费用路线；提前停止方式有 253 次提前终止，
但没有一次返回超过一条负费用路线，LP 迭代增加，未选作下一阶段的候选。
这是一个单种子探索，用于选择批量策略做后续成对复核，不能单独作为普遍加速结论。
Gurobi Work 是工作量指标，不等同于秒。

## 正式端到端对照

基线为上一轮分仓对偶约束混合流程，候选为同一流程加批量定价。
两者都使用列生成、实体车辆路线池初解、16 条对偶约束和完整联合 MILP，
每对输入、初解、种子、预算和冻结源码一致，seed 43 反转执行次序。
整个流程共享总墙钟预算，包括建模、根节点、初解排程、审计和完整 MILP。
定价方法均为 MILP，生产默认策略不改变。

`final_short/`：20 客户，种子 42/43，总预算 30 秒，根节点上限 10 秒、路线池上限 3 秒。

| 种子 | 原距离 | 批量距离 | 原下界 | 批量下界 | 原 Gap | 批量 Gap | 原/批量根节点秒数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 42 | 535.2960 | 535.2960 | 502.6851 | 503.1271 | 6.0921% | 6.0096% | 5.095 / 3.536 |
| 43 | 535.2960 | 535.3507 | 502.6851 | 502.6542 | 6.0921% | 6.1075% | 5.775 / 3.078 |

20 客户 seed 42 的距离相同、Gap 略降；seed 43 的距离与 Gap 略有退步。
两个种子均得到同一松弛最优值和 543.0707 的路线池初解，但最终 16 个对偶平面与后续搜索不同。
根节点提速不保证可行解质量改善。

`final_long/`：30 客户，种子 42/43，总预算 60 秒，根节点上限 30 秒、路线池上限 5 秒。

| 种子 | 原距离 | 批量距离 | 原下界 | 批量下界 | 原 Gap | 批量 Gap | 原/批量根节点秒数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 42 | 666.5352 | 730.1087 | 567.2139 | 609.4555 | 14.9011% | 16.5254% | 30.004 / 17.880 |
| 43 | 687.4789 | 693.7822 | 563.1347 | 609.4555 | 18.0870% | 12.1546% | 30.005 / 20.784 |

批量方式两个种子均完成根节点松弛，原方式均耗尽 30 秒上限，但批量流程的最终距离两组都退步。
较大的路线池生成 2820 个放置变量，两次在 5 秒预算内均只得到未证明路线池最优的初解；
原流程的路线池内部最优在 5 秒内完成。路线池内部状态只用来诊断初解搜索，仍不转移任何全局证明。
最终 Gap 为一组改善、一组退步。单看根节点时间会掩盖此代价。

## 复用节省时间的追加试验

实现显式 `reuse_root_time=True`：若根节点实际耗时低于所分配上限，额外将
`min(原路线池上限, 未用完的根节点时间)` 分给路线池排程。
因此原路线池上限 5 秒时最多增至 10 秒；这个可选行为不改变原默认上限。
路线池和完整 MILP 仍共享 `time_limit` 的总截止时刻，不能使总预算延长。

`reuse_long/` 保留 30 客户两个种子的新增成对试验，总预算仍为 60 秒，
基线是原分仓对偶流程，候选为批量定价加上述预算复用。
这是在前一轮暴露初解代价后做的新参数试验，前一轮退步未覆盖或丢弃。
预算复用专项检查覆盖不同根节点耗时和默认关闭状态，实际核对两个阶段的截止时刻、
完整 MILP 得到的剩余预算及有效初解保留，未用单纯重复公式的检查替代集成验证。

| 种子 | 原距离 | 批量复用距离 | 原下界 | 新下界 | 原 Gap | 新 Gap | 原/新根节点秒数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 42 | 732.9778 | 660.6786 | 602.3340 | 609.4555 | 17.8237% | 7.7531% | 30.003 / 15.187 |
| 43 | 680.4876 | 687.5427 | 609.4556 | 609.4555 | 10.4384% | 11.3574% | 29.478 / 19.171 |

追加试验中，批量方式的路线池实际耗时 6.277 / 6.213 秒，在复用后的 10 秒上限内完成内部最优，
但只把其审计通过的可行解交给完整求解器。与原流程比较仍为一组改善、一组退步。
seed 42 的最终距离 660.6786、Gap 7.7531% 是本轮 30 客户试验中最好方案；这不是原问题最优证明。
seed 43 仍得到不同的路线池，因此完成更大的路线池内部最优，也不能保证原问题的初解更好。

正式固定预算共 4 组，Gap 为 2 改善 / 2 退步，距离无改善 / 1 相同 / 3 退步。
追加预算复用为 1 改善 / 1 退步。所有 6 组的批量根节点更快，且在每个规模都达到该规模的完整松弛最优值；
30 客户批量四次均在 30 秒内完成，原四次只有追加 seed 43 完成。
这些有限样本支持根节点效率改善，尚不支持稳定改善整个精确求解流程。

## 采用决策与证据保存

批量定价、提前停止和预算复用均保持显式实验选项，生产编号模型和原混合入口默认策略不改变。
提前停止因单种子探索中迭代过多，未进入正式端到端试验。
批量加预算复用可供 M1/M2 带已审计初解的试验使用，完整 MILP 始终保留最好输入可行解。
尚未测得原问题最优性证明时间缩短，不推广到 M3/M4。

本轮 12 个整数结果全部通过独立审计和两周期重放，原问题最优证明数为 0；
3 个根节点诊断使用相同松弛最优值，不能列入整数证明数或可行解审计数。
所有输入、源码、初解、参数、日志及成功/退步结果均在上述四个批次保存，顺序独立进程、无失败重试。
`summary.json` 和 `result_manifest.json` 汇总成对结果、工作量、审计和文件 SHA256。

固定预算短/长批次的源码相同；与根节点探索相比存在三处其他聊天的未执行模块差异：
ALNS `evaluator.py`、`single_batch_cache.py` 和多批次覆盖试验脚本。
追加试验另包含本轮预算复用的 `single_batch_columns.py` 和运行器变更。
这些差异全部记录；每个批次内部两个方法均使用同一冻结源码。
全库回归前另记录其他聊天的三个多批次精确模块变更，未修改或回退这些文件。

## 验证与使用

新增测试覆盖两个阶段、不同随机有向非度量矩阵和时间惩罚，逐条核对解池路线、费用和时刻，
并核对完整/未完成定价下界、提前结束后目标变化、零剩余预算、参数恢复、
全部分仓和路线拆分的对偶约束、根节点松弛一致性以及完整 MILP 的原问题最优性。
穷举解池模式只在微型测试中使用；实际批量流程保持默认搜索模式。
修正解池时刻重复后，专项检查 363 项通过；增加每种分仓全部拆分的核对和预算复用后，
专项 **369 passed in 5.94s**；全库 **1469 passed, 14 warnings in 55.63s**。
14 条警告来自既有 Matplotlib/Pyparsing 弃用提示；验证前后记录的 36 个源码与专项测试文件 SHA256 无变化。
原始日志、5 个专项测试快照及验证哈希保存在结果目录的 `targeted_tests.log`、
`reuse_targeted_tests.log`、`full_tests.log`、`tests_snapshot/` 和 `validation.json`。
计时试验均未与本聊天的求解测试或探针重叠。回归后再次核对全部结果、参数、日志和初解哈希。

```python
from models.routing.solvers.exact.single_batch_columns import solve_with_route_bound

result = solve_with_route_bound(problem, time_limit=60, root_time=30,
    pool_time=5, pricing_method='mip', mip_strategy='pool',
    dual_cuts=True, reuse_root_time=True, initial_result=audited_start)
```

```sh
.venv/bin/python experiments/runs/run_single_batch_columns.py \
  --frozen-runs output/experiments/single_batch_site_flow_20261001/final/zhou_Set2_100-5-1b_subset30_M2 \
  --methods columns_dual columns_batch --seeds 42 43 \
  --total-time 60 --root-time 30 --pool-time 5 --pricing-method mip \
  --output output/experiments/single_batch_pricing_new_long

.venv/bin/python experiments/runs/run_single_batch_columns.py \
  --frozen-runs output/experiments/single_batch_site_flow_20261001/final/zhou_Set2_100-5-1b_subset30_M2 \
  --methods columns_dual columns_batch_reuse --seeds 42 43 \
  --total-time 60 --root-time 30 --pool-time 5 --pricing-method mip \
  --output output/experiments/single_batch_pricing_new_reuse

.venv/bin/python -m pytest tests/routing/test_single_batch_pricing_pool.py \
  tests/routing/test_single_batch_columns.py tests/routing/test_single_batch_time_budget.py \
  tests/routing/test_single_batch_route_pool.py tests/routing/test_single_batch_dual_cuts.py -q
```
