# M6–M8：批次数与业务决策的统一 MILP

更新：2026-10-19。论文的求解主线为完整数学模型直接交给 Gurobi，并与 ALNS 比较。
M1–M5 的批次规则仍由各模型定义；M6–M8 将批次数选择纳入同一个 MILP，不在求解器外逐个求解 K。
默认不使用构造初解、变量提示、自定义割、外部下界证书、分仓域搜索或自适应 K 调度。
Gurobi 自带的预处理、割和平衡搜索保持默认设置。

## 1. 批次数及业务允许域

周期为 \([t_0,t_1]\)，长度 \(H=t_1-t_0\)，每批两个阶段的最短时长为 \(m>0\)。
允许批次数为有限集合

\[
\mathcal K\subseteq\{1,\ldots,\lfloor H/(2m)\rfloor\}.
\]

默认采用完整正整数域；显式输入列表时，证明范围就是该列表。每个计入 K 的批次必须
配送至少一笔订单、向总仓回运至少一个空箱；允许回收此前批次订单的箱子，禁止空批次。
保留全部输入候选，即使某些 K 因订单数量或时间限制不可行，也由统一模型排除。

## 2. 变量与模型结构

引入批次数选择变量 \(\delta_k\in\{0,1\}\)，满足

\[
\sum_{k\in\mathcal K}\delta_k=1,\qquad
K=\sum_{k\in\mathcal K}k\delta_k.
\tag{1}
\]

对每个 k 建立一组业务变量 \(x^k\)，包括订单分批、两级车辆路线、实际作业时刻、
回收资格与数量、清洗释放、容器池及日历。只有被选择的一组生效；其余组全部取零。
这是一个分块的扩展 MILP 表达，不是对各组分别调用求解器。

| 变量组 | 业务含义 |
| --- | --- |
| \(y^k_{ib}\) | 订单 i 是否分配给第 b 批 |
| \(z^k_b\) | 第 b 批启用，满足 \(z^k_b=\delta_k\) |
| \(N^k\)、\(N\) | 分块容器池与实际容器池，\(N=\sum_kN^k\) |
| 路线弧、访问和趟次启用变量 | 两级车辆实际路线和任务覆盖 |
| 物理车辆启用变量 | 每辆车在整个周期是否使用 |
| 作业时刻、载荷和因果序位 | 时间窗、容量、先送后取与车辆串行 |
| 回收资格、回运量和清洗释放量 | 截止全取、一级回运和清洁库存循环 |
| \(s^k_b,e^k_b,c^k_b\) | 批次开始、结束及回运资格截止 |

各变量的完整定义及业务行由本模型的 `variables.py`、`constraints.py` 和 `objective.py`
给出；下面用矩阵写出将这些完整业务行统一起来的精确表达，并说明日历差异。

## 3. 完整业务约束的统一表达

设固定 k 时的全部线性业务行及有限变量界为

\[
A_kx^k\le b_k,\quad E_kx^k=d_k,\quad
\ell^k\le x^k\le u^k.
\]

大于等于行可反向写成小于等于行。整数变量保持整数，二元变量保持二元。
统一模型使用

\[
A_kx^k\le b_k\delta_k,\qquad
E_kx^k=d_k\delta_k,\qquad
\ell^k\delta_k\le x^k\le u^k\delta_k,
\quad k\in\mathcal K.
\tag{2}
\]

例如，每个订单唯一分批变为 \(\sum_b y^k_{ib}=\delta_k\)，
每批非空变为 \(\sum_i y^k_{ib}\ge\delta_k\)，容器池满足
\(N_{\min}\delta_k\le N^k\le N_{\max}\delta_k\)。
不能把所有固定 k 模型的原约束直接堆叠，否则会错误地要求全部 k 同时可行。

若原业务行采用指示约束

\[
v^k=\beta\ \Longrightarrow\ a^\top x^k\ \diamond\ r,
\qquad\beta\in\{0,1\},\quad\diamond\in\{\le,=,\ge\},
\]

则统一表达为

\[
v^k=\beta\ \Longrightarrow\ a^\top x^k-r\delta_k\ \diamond\ 0.
\tag{3}
\]

指示条件中的变量也是该组变量。未选组全部为零，所以即使零值触发指示条件，其结论也是
\(0\diamond0\)，不会错误限制选中的组。Gurobi 原生处理线性指示约束；这里没有自定义切割。

业务行完整包含：订单唯一配送和回收、一级供货与数量平衡、二级任务覆盖、路径流平衡及
无断开子环、重量与体积容量、顾客时间窗、订单释放与最短使用时长、实际供货先于二级出发、
同车连续趟次的串行与周转、截止前卸回前置仓的空箱全取、一级回运、总仓清洗释放、领箱库存
充足、周期末回收清洗完成以及车辆启用计费。未通过删去同步或库存来简化可行域。

## 4. 三种日历的区别

以下以物理时间书写；未选分块的所有常数项均乘 \(\delta_k\)。

**M6：等长批次、共同相对截止。**

\[
s_b^k=\left(t_0+(b-1)H/k\right)\delta_k,\quad
e_b^k=\left(t_0+bH/k\right)\delta_k,
\]
\[
c_b^k=s_b^k+D^k,\qquad
m\delta_k\le D^k\le(H/k-m)\delta_k.
\tag{4}
\]

**M7：等长批次、独立截止。** 开始和结束同式 (4)，但每批有独立 \(D_b^k\)：

\[
c_b^k=s_b^k+D_b^k,\qquad
m\delta_k\le D_b^k\le(H/k-m)\delta_k.
\tag{5}
\]

**M8：可变批次长度、独立截止。** 边界 \(T_0^k,\ldots,T_k^k\) 为变量：

\[
T_0^k=t_0\delta_k,\quad T_k^k=t_1\delta_k,\quad
s_b^k=T_{b-1}^k,\quad e_b^k=T_b^k,
\]
\[
s_b^k+m\delta_k\le c_b^k\le e_b^k-m\delta_k.
\tag{6}
\]

由此保留覆盖完整周期、连续且不重叠、每批至少两个最短阶段的规则。
M8 没有使用预先枚举的边界网格，所有原允许整数边界都在模型内。

M6/M7 若 H/k 不能在原整数时钟上精确表示，沿用原规则：第 k 组以
\(f_k=k/\gcd(H_{\mathrm{ticks}},k)\) 细分时钟。时窗、释放、清洗和服务时长同步放大，
工时成本按每个新 tick 的物理分钟换算。各组只通过无量纲选择变量、实际成本和容器池连接，
不混加不同组的时间刻度，也不需要对所有 k 求巨大公倍数。该处理保留原允许域；
最优性结论仍针对声明的离散时间精度。

## 5. 目标函数

令固定 k 的完整成本为 \(c_k^\top x^k+c_{0k}\)。统一目标为

\[
\min\ \sum_{k\in\mathcal K}\left(c_k^\top x^k+c_{0k}\delta_k\right).
\tag{7}
\]

成本包含容器池、两级距离、占用工时、趟次、物理车辆以及配置中的固定周转费用。
原目标的常数费用也必须乘选择变量，不能对每个未选 k 重复收费。
M1–M5 继续采用各自原目标，跨模型经济比较仍使用独立的共同成本评价。

## 6. 等价性与证明范围

由式 (1)，恰好一个 \(\delta_{k^*}=1\)。式 (2) 强制其他分块变量为零，
选中分块恢复全部固定 \(k^*\) 的原约束；式 (3) 同样恢复原指示约束。
因此统一模型中的任何可行解都对应一个原允许批次数下的完整方案。
反之，任意原可行方案都可以放入对应分块，其余分块设为零，即构成统一模型可行解。
式 (7) 保持成本，故两者最优值相同。

程序对所有分块只建模、不求解，最终对统一模型调用一次 `optimize()`。
只在整个模型返回最优且方案通过原业务审计后报告全局最优；求解器下界直接覆盖全部 K。
建模超时则不求解残缺模型、不发布残缺候选域的界或最优性声明。
无解的限时状态与已证明不可行保持区分。

## 7. 实现与实验使用

- 各编号 `exact/model.py`：默认返回统一模型；显式 `batch_count=k` 只用于固定 k 诊断。
- 各编号 `exact/formulation.py`：各自完整业务块的构造。
- `models/common/gurobi_disjunction.py`：按式 (2)–(3) 线性装配，不包含搜索算法。
- `models/common/cycle/unified_milp.py`：选择变量、单次求解和完整结果审计。
- 各编号 `exact/solver.py`：默认 `strategy='milp'`；`auto` 为直接求解的兼容别名。

```python
from models.routing.model_8.exact import solve_exact
result = solve_exact(problem, time_limit=300, seed=42)
# result['cycle_batch_count']：Gurobi 选定的 K
# result['info']['best_bound']：同一个完整模型的全局下界
# result['info']['exact_strategy'] == 'gurobi_unified_milp'
```

正式精确结果不再生成逐 K 求解的 `count_search`/`count_search_visits`。
用 `info.batch_count_candidates`、`complete_count_domain`、整体模型规模和整体求解时间记录实验。
ALNS 仍可保留自己的跨 K 搜索记录。旧增强算法和分解实验已从当前代码移除，源码及专用测试/驱动见
[研究归档](../archive/exact_research_20261019/README.md)。已有冻结结果不改写，不混作当前直接 Gurobi 的结果。

这一表达优先保证业务等价和模型清晰，规模约为各 k 业务块规模之和；不会保证比原外层搜索更快。
候选域很大时可能在建模阶段耗尽预算。论文实验应如实报告建模时间、求解时间、可行率和 gap，
不能为了得到最优结果而暗中缩小 K 域。较大规模求解由 ALNS 承担。


## 8. 本次验证

2026-10-19，两组检查分别通过 273 项和 110 项（包含重叠的实验驱动检查）。
覆盖 M1–M8 默认直接入口、M6–M8 与逐 K 固定模型的最优值一致、独立小规模日历穷举、
Gurobi 单次调用且没有初解/回调/外部证书、全部候选不可行、建模中断不误报证明、
非整除时钟的物理费用、M7 延迟装货扩展、两周期业务审计、CLI 报告和正式 worker 输出。

主要测试文件：`tests/routing/test_unified_batch_milp.py`、`test_milp_baseline.py`、
`test_model6_uniform.py`、`test_model78_calendar.py`、`test_model78_independent_oracle.py`，
以及 `tests/test_thesis_completion_driver.py`。
这些是实现与等价性验证；本次没有重跑正式基准数据，也未据此声称大规模性能提升。
