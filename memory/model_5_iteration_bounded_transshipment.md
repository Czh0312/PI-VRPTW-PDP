---
name: model-5-iteration-bounded-transshipment
description: Model 5 受控迭代中转 ALNS 设计：每轮 ALNS 候选解内嵌中转局部搜索，并用收益区间、数量上限和回滚机制抑制中转过度累积
metadata:
  type: project
---

# Model 5：受控迭代中转 ALNS

## 1. 核心判断

这个思路是合理的，但不能简单回到 Model 3。

Model 3 的问题不是“中转放在 ALNS 迭代中”本身不合理，而是中转被当成了贪心累积操作：每次只要发现一个正收益中转就加入，撤销频率低，且后续评估容易把已经累积的绕路当成既定事实，最终出现“单个看是正收益，整体却变差”的现象。

Model 5 的改进思想是：

```text
每次 ALNS 产生候选解 candidate 后，
先在 candidate 内部做一次受控中转局部搜索，
只接受满足收益区间和可行性约束的中转组合，
再把“路线 + 中转”作为一个整体交给 ALNS 接受准则判断。
```

也就是说，中转不再是独立于 ALNS 接受机制之外的永久累积动作，而是候选解的一部分。若该 candidate 没有被接受，本轮新增的中转也随 candidate 一起丢弃。

## 2. 与已有模型的区别

| 模型 | 中转时机 | 中转接受方式 | 主要风险 |
|---|---|---|---|
| Model 2 | 无中转 | 无 | 缺少中转收益 |
| Model 3 | ALNS 迭代中 | 正收益即加入，撤销靠 destroy | 容易过度累积 |
| Model 4 | ALNS 结束后 | 固定路线后处理改进 | 稳定，但中转无法反向影响路线搜索 |
| Model 5 | 每轮 candidate 内 | 收益区间 + 数量上限 + 整体 ALNS 接受 | 稳定性依赖阈值设计 |

Model 5 可以理解为 Model 3 和 Model 4 的折中：

- 保留 Model 3 的优点：路线搜索过程中能感知中转带来的结构变化。
- 吸收 Model 4 的优点：中转必须经过严格可行性检查和真实总成本评估。
- 新增控制机制：中转只在当前 candidate 上生效，避免无控制地滚雪球。

## 3. 中转定义

中转定义沿用 Model 3/4 的统一版本：

```text
顾客 A 的 pickup 任务 Cp_A 原本在 A 处取空容器。
若选择中转到顾客 B，则 Cp_A 仍属于原 pickup 车辆路线，
但 Cp_A 的物理取货位置由 A 改为 B。
另一辆车负责把 A 处空容器搬运到 B 处。
```

中转后包含两类变化：

1. 受益车辆路线变化：Cp_A 的物理访问位置从 A 变成 B。
2. 搬运车辆路线变化：插入 `TRANS_PICK_Cp_A@A` 和 `TRANS_DROP_Cp_A@B`。

所有中转都必须重新检查：

- 二级车辆载重/体积约束；
- 取送先后关系；
- 顾客时间窗；
- 卫星站二级车出发时间与一级车回收衔接；
- 搬运车取放顺序；
- 中转后总目标值。

## 4. 收益度量

对候选中转方案 `r`，定义：

```text
gain(r | x) = C(x) - C(T_r(x))
```

其中：

- `x`：当前 ALNS 候选解；
- `T_r(x)`：在 `x` 上应用中转方案 `r` 后的解；
- `C(.)`：总目标函数，可使用当前项目中的总距离/总成本评价器；
- `gain > 0` 表示中转使目标值下降。

为了避免 Model 3 的贪心累积问题，Model 5 不只看单个受益路线的节省，而必须用应用中转后的真实总成本计算 `gain`。

## 5. 收益区间接受规则

设本轮搜索到的最佳中转收益为：

```text
gain_best = max gain(r | x)
```

构造可接受中转集合：

```text
A(x) = { r | r feasible,
             gain(r | x) >= theta_min,
             gain(r | x) >= alpha * gain_best }
```

建议参数：

- `theta_min`：绝对最小收益，例如 `0.01` 或 `0.1`；
- `alpha`：相对收益比例，例如 `0.6 ~ 0.9`；
- `gain_best`：本轮候选池中的最大真实净收益。

这样就实现了“在一定范围内的中转收益都可以接受”：

- 不必每次都选唯一最优中转，保留搜索多样性；
- 也不会把收益过小的中转无限加入；
- 若多个中转收益接近，可以从 `A(x)` 中随机选择，或按收益加权选择。

进一步可以加入动态阈值：

```text
theta_t = max(theta_min, beta * temperature_t)
```

ALNS 前期温度高，允许更多探索；后期温度低，只有更明确的正收益中转才被接受。

## 6. 单轮中转数量控制

每次 ALNS 迭代中，中转局部搜索不应无限循环，建议设置：

```text
max_moves_per_iteration = 1 或 2
max_total_transshipments = ceil(gamma * pickup_count)
max_transshipments_per_satellite = 2 ~ 5
```

单轮流程：

```text
for k = 1 ... max_moves_per_iteration:
    搜索可行中转候选
    计算每个候选的真实 gain
    构造可接受集合 A(x)
    if A(x) 为空:
        break
    从 A(x) 中选择一个中转 r
    临时应用 r
    if 解不可行或真实总成本不满足阈值:
        回滚 r
        break
```

关键点：每应用一个中转后，下一次搜索必须基于新的 candidate 重新计算真实成本，而不是继续使用旧的边际估计。

## 7. ALNS 主流程

Model 5 的 ALNS 框架可以写为：

```text
输入：初始解 x0，最大迭代次数 N，温度 T，破坏算子集 D，修复算子集 R
输出：最优解 x_best

x_current = x0
x_best = x0

for t = 1 ... N:
    1. 复制当前解：
       x_candidate = copy(x_current)

    2. 选择 destroy/repair 算子：
       d in D, r in R

    3. 执行路线破坏与修复：
       x_candidate = r(d(x_candidate))

    4. 同步并清理中转：
       删除因路线变化而失效的中转记录
       恢复 task_to_phys 映射一致性

    5. 执行受控中转局部搜索：
       x_candidate = bounded_transshipment_search(x_candidate)

    6. 计算完整目标值并检查全局可行性：
       C_candidate = C(x_candidate)

    7. 用 ALNS 接受准则判断：
       if C_candidate < C_current:
           接受 x_candidate
       else:
           以 exp(-(C_candidate - C_current) / T) 的概率接受

    8. 若接受，则路线和中转一起进入 x_current；
       若拒绝，则本轮路线变化和中转变化一起丢弃。

    9. 若 x_current 优于 x_best：
       x_best = copy(x_current)

    10. 更新算子权重和温度。
```

## 8. 受控中转局部搜索伪代码

```text
function bounded_transshipment_search(x, evaluator, T):
    moves = 0

    while moves < max_moves_per_iteration:
        plans = sample_transshipment_candidates(x)
        accepted_list = []
        best_gain = -inf

        for plan in plans:
            x_tmp = copy(x)
            apply(plan, x_tmp)

            if not feasible(x_tmp):
                continue

            gain = C(x) - C(x_tmp)

            if gain > best_gain:
                best_gain = gain

            record(plan, gain, x_tmp)

        theta = max(theta_min, beta * T)

        for recorded plan:
            if gain >= theta and gain >= alpha * best_gain:
                accepted_list.append(plan)

        if accepted_list is empty:
            break

        plan = roulette_select(accepted_list, weight = gain)
        apply(plan, x)

        if not feasible(x):
            rollback(plan, x)
            break

        moves += 1

    x.cost = C(x)
    return x
```

## 9. 推荐参数

| 参数 | 含义 | 建议值 |
|---|---|---|
| `transshipment_interval` | 每隔多少轮触发中转 | `1` 或 `5` |
| `max_moves_per_iteration` | 每轮最多加入几个中转 | `1` |
| `theta_min` | 最小绝对收益 | `0.01 ~ 0.1` |
| `alpha` | 接受候选需达到最佳收益的比例 | `0.7` |
| `max_total_transshipments` | 全局中转上限 | `pickup_count * 0.05 ~ 0.15` |
| `max_transshipments_per_satellite` | 单卫星中转上限 | `2 ~ 5` |
| `time_budget_per_iteration` | 每轮中转搜索时间 | `0.05s ~ 0.2s` |
| `position_top_k` | 插入位置候选数 | `3` |

如果算例规模较大，建议从保守设置开始：

```text
transshipment_interval = 5
max_moves_per_iteration = 1
alpha = 0.8
max_total_transshipments = pickup_count * 0.05
time_budget_per_iteration = 0.05s
```

## 10. 为什么 Model 5 可能优于 Model 4

Model 4 的中转只在最终路线固定后发生，因此它只能在已有路线结构上找改进。

Model 5 在每次 ALNS 迭代中嵌入中转后，路线搜索可能主动适应中转结构。例如：

- 某个 pickup 改到更近位置后，repair 算子后续可能把相关 delivery/pickup 调整到更好的路线中；
- 中转改变局部距离结构后，worst removal 或 shaw removal 可能更容易识别新的重构机会；
- ALNS 接受准则可以保留一些短期不显著但后续有潜力的“路线 + 中转”组合。

## 11. 主要风险与控制

### 风险 1：退化成 Model 3 的过度累积

控制方式：

- 中转只作用于 candidate；
- candidate 拒绝则中转丢弃；
- 设置全局和单卫星中转上限；
- 每轮最多加入 1 个中转；
- 使用真实总成本而非局部边际估计。

### 风险 2：每轮中转搜索太慢

控制方式：

- 抽样 pickup；
- 抽样目标位置；
- 只检查前 `top_k` 个插入位置；
- 设置极短时间预算；
- 每隔 `transshipment_interval` 轮触发，而不是每轮都触发。

### 风险 3：小收益中转干扰路线结构

控制方式：

- 设置 `theta_min`；
- 设置 `alpha * gain_best`；
- 后期提高收益门槛或降低中转触发频率；
- 定期执行 pruning，删除贡献低或导致路线绕行大的中转。

## 12. 可以写成论文中的模型五描述

Model 5 可以命名为：

```text
模型五：嵌入受控中转搜索的自适应大邻域算法
```

或：

```text
Model 5: ALNS with Bounded In-Iteration Transshipment Search
```

论文式表述：

```text
模型五在模型四两阶段思想的基础上，将中转决策由最终后处理阶段提前到
ALNS 的每一次候选解生成过程中。与模型三不同，模型五并不将所有正收益
中转立即永久加入当前解，而是把中转作为候选解内部的局部改进算子，并通
过收益区间、单轮操作上限、全局中转上限和 ALNS 接受准则共同控制中转的
引入。该方法既允许路线搜索过程感知中转带来的成本结构变化，又避免中转
操作在迭代过程中无约束累积，从而在搜索多样性与解稳定性之间取得平衡。
```

## 13. 实现落点

已新增：

```text
models/model_5/
├── __init__.py
└── alns_solver.py
```

实现时可以复用：

- Model 3 的中转候选生成和虚拟搬运节点逻辑；
- Model 4 的严格后处理中转可行性检查思路；
- `models/alns_engine.py` 的 hook 机制。

关键 hook 位置：

```text
after_operators:
    清理失效中转并同步 task_to_phys

after_cost:
    对 candidate 执行 bounded_transshipment_search
    重新计算 candidate.cost

after_accept:
    只有 candidate 被 ALNS 接受后，才同步 evaluator.task_to_phys
```

最重要的实现原则：

```text
新增中转必须属于 candidate。
candidate 未被接受，中转不得污染 current_solution。
```

## 14. 当前实现状态

已实现 Model 5 ALNS 版本，精确求解版本暂未实现。

已修改/新增文件：

- `models/model_5/alns_solver.py`：Model 5 每轮中转试探 ALNS；
- `models/model_5/__init__.py`：模型包初始化；
- `utils/task_builder.py`：将 Model 5 任务构建逻辑并入 Model 2/3/4 的取送货任务；
- `main.py`：挂载 Model 5 ALNS 入口；
- `experiments/run_model345_comparison.py`：非北京算例下 Model 3/4/5 ALNS 对比脚本。

当前实现的关键设定：

```text
1. 不在初始解上预先执行中转优化；
2. 不把 transshipment_destroy 放入普通 destroy 算子；
3. 每轮 destroy/repair 后对 candidate 执行一次中转试探；
4. candidate 未被 ALNS 接受时，本轮中转自动丢弃；
5. 默认 transshipment_interval = 1；
6. 默认 transshipment_time_budget = 0.05s。
```

## 15. 初步实验结果

实验脚本：

```text
python3 experiments/run_model345_comparison.py
```

### 实验 1：10_customers/C101.csv

参数：

```text
iterations = 1000
time limit = 30s/model
seed = 42
transshipment_time_budget = 0.05s
max_total = 3
max_per_satellite = 2
```

结果：

| 模型 | cost | 中转数 | 中转试探次数 | 中转成功加入候选次数 | 耗时 |
|---|---:|---:|---:|---:|---:|
| Model 3 | 609.70 | 2 | - | - | 1.29s |
| Model 4 | 568.18 | 0 | - | - | 1.30s |
| Model 5 | 578.83 | 2 | 1000 | 17 | 2.02s |

输出文件：

```text
output/experiments/model345_10_customers_C101_iter1000_seed42_summary.csv
```

### 实验 2：50_customers/C101.csv

参数：

```text
iterations = 300
time limit = 30s/model
seed = 42
transshipment_time_budget = 0.05s
max_total = 8
max_per_satellite = 3
```

结果：

| 模型 | cost | 中转数 | 中转试探次数 | 中转成功加入候选次数 | 耗时 |
|---|---:|---:|---:|---:|---:|
| Model 3 | 1458.49 | 6 | - | - | 2.30s |
| Model 4 | 1389.50 | 0 | - | - | 2.13s |
| Model 5 | 1518.29 | 8 | 300 | 18 | 3.14s |

输出文件：

```text
output/experiments/model345_50_customers_C101_iter300_seed42_summary.csv
```

## 16. 实验结论

当前 Model 5 已经满足“每次迭代都进行一次中转试探”的要求：

- 10 客户 1000 轮实验中，Model 5 试探 1000 次；
- 50 客户 300 轮实验中，Model 5 试探 300 次。

但从初步结果看，朴素的“每轮都试探 + 正收益即加入 candidate”并没有稳定超过 Model 4。可能原因：

1. 每轮试探增加了搜索开销；
2. 中转虽然在 candidate 内是正收益，但会改变后续路线结构，可能干扰 ALNS 收敛；
3. 当前中转试探仍偏贪心，只要超过 `min_net_saving` 就应用，缺少相对收益区间筛选；
4. Model 4 在这些 C101 算例上本身很稳，后处理中转数为 0，说明当前算例中可用中转机会有限。

下一步更合理的 Model 5 调参方向：

```text
1. 引入 alpha * gain_best 的相对收益阈值；
2. 每轮仍试探，但不一定应用中转；
3. 对中转应用增加更高 theta_min；
4. 对已接受解定期 pruning 低贡献中转；
5. 用多算例、多 seed 汇总平均表现，而不是只看单个 C101。
```

## 17. 中转试探深度方案对比

为回答“每轮只做 1 次中转是否过弱、是否应允许一定范围内多次中转”的问题，已将 Model 5 扩展为可配置试探深度：

```text
transshipment_probe_depth = K
transshipment_probe_schedule = fixed / decreasing
transshipment_probe_selection = last / best_prefix
```

已测试方案：

| 方案 | 含义 |
|---|---|
| `m5_k1_single` | 每轮最多 1 次中转，当前基础版 |
| `m5_k2_chain` | 每轮从少到多连续试探，最多 2 次 |
| `m5_k3_chain` | 每轮从少到多连续试探，最多 3 次 |
| `m5_k3_best_prefix` | 构造 0/1/2/3 次中转前缀，保留其中最好解 |
| `m5_k3_decreasing` | 前期最多 3 次，中期 2 次，后期 1 次 |

实验脚本：

```text
python3 experiments/run_model5_variant_comparison.py
```

### 有效实验结果

#### 10_customers/C101.csv

参数：`iterations=500, seed=42, max_total=3, max_per_satellite=2`

| 方案 | cost | 中转数 | 试探次数 | 试探步数 | 成功加入候选次数 |
|---|---:|---:|---:|---:|---:|
| Model 3 | 579.06 | 0 | - | - | - |
| Model 4 | 591.38 | 0 | - | - | - |
| `m5_k1_single` | 601.20 | 1 | 500 | 500 | 8 |
| `m5_k2_chain` | 599.49 | 2 | 500 | 514 | 16 |
| `m5_k3_chain` | 599.49 | 2 | 500 | 516 | 16 |
| `m5_k3_best_prefix` | 599.49 | 2 | 500 | 516 | 16 |
| `m5_k3_decreasing` | 599.49 | 2 | 500 | 516 | 16 |

#### 10_customers/R101.csv

参数：`iterations=500, seed=42, max_total=3, max_per_satellite=2`

| 方案 | cost | 中转数 | 试探次数 | 试探步数 | 成功加入候选次数 |
|---|---:|---:|---:|---:|---:|
| Model 3 | 596.16 | 0 | - | - | - |
| Model 4 | 596.16 | 0 | - | - | - |
| `m5_k1_single` | 603.84 | 0 | 500 | 500 | 10 |
| `m5_k2_chain` | 610.37 | 2 | 500 | 512 | 12 |
| `m5_k3_chain` | 610.37 | 2 | 500 | 512 | 12 |
| `m5_k3_best_prefix` | 610.37 | 2 | 500 | 512 | 12 |
| `m5_k3_decreasing` | 610.37 | 2 | 500 | 506 | 12 |

#### 10_customers/RC101.csv

参数：`iterations=500, seed=42, max_total=3, max_per_satellite=2`

| 方案 | cost | 中转数 | 试探次数 | 试探步数 | 成功加入候选次数 |
|---|---:|---:|---:|---:|---:|
| Model 3 | 654.84 | 0 | - | - | - |
| Model 4 | 665.22 | 0 | - | - | - |
| `m5_k1_single` | 635.35 | 0 | 500 | 500 | 7 |
| `m5_k2_chain` | 662.48 | 2 | 500 | 506 | 6 |
| `m5_k3_chain` | 662.48 | 2 | 500 | 506 | 6 |
| `m5_k3_best_prefix` | 662.48 | 2 | 500 | 506 | 6 |
| `m5_k3_decreasing` | 662.48 | 2 | 500 | 506 | 6 |

#### 50_customers/C101.csv

参数：`iterations=300, seed=42, max_total=8, max_per_satellite=3`

| 方案 | cost | 中转数 | 试探次数 | 试探步数 | 成功加入候选次数 |
|---|---:|---:|---:|---:|---:|
| Model 3 | 1489.93 | 6 | - | - | - |
| Model 4 | 1389.50 | 0 | - | - | - |
| `m5_k1_single` | 1518.29 | 8 | 300 | 300 | 18 |
| `m5_k2_chain` | 1616.38 | 8 | 300 | 320 | 25 |
| `m5_k3_chain` | 1686.79 | 8 | 300 | 310 | 11 |
| `m5_k3_best_prefix` | 1686.79 | 8 | 300 | 310 | 11 |
| `m5_k3_decreasing` | 1686.79 | 8 | 300 | 310 | 11 |

### 结论

1. “每轮只做 1 次中转试探”不是太弱，反而是当前最稳的 Model 5 方案。
2. `K=2` 在 10 客户 C101 上略优于 `K=1`，但在 R101、RC101 和 50 客户 C101 上变差。
3. `K=3` 没有带来额外收益，结果通常与 `K=2` 相同或更差。
4. `best_prefix` 与 `chain` 结果相同，原因是当前 `optimize_transshipment` 每次只接受正净收益中转，连续前缀成本理论上单调下降；但这种“局部下降”不代表对 ALNS 全局搜索更好。
5. `decreasing` 没有明显改善，说明问题不只是前后期深度，而是多中转连续应用本身容易扰乱路线结构。

当前建议：

```text
Model 5 主方案保留 K=1。
K=2 可作为对照实验。
不建议把 K=3 或更深作为主方案。
```

如果后续继续优化 Model 5，更值得做的不是增加连续中转次数，而是：

```text
每轮仍试探一次或少量次数；
但对候选中转使用更严格的收益筛选和随机化接受；
避免把局部正收益中转过早固化到搜索路径里。
```

## 18. 与 Model 2/3/4 的统一对比

根据最新讨论，Model 5 的准确结构应表述为：

```text
每一次 ALNS 外层迭代生成一个 candidate 后，
对该 candidate 执行一个内层中转迭代过程。
内层最多执行 H 次中转迭代，达到上限或没有可接受中转时停止。
随后把中转迭代后的完整 candidate 交给 ALNS 接受准则，
决定是否进入下一轮外层 ALNS。
```

当前代码中的对应参数：

```text
transshipment_probe_depth = H
```

最新实验已把 Model 2/3/4 加入同一张对比表，脚本为：

```text
python3 experiments/run_model5_variant_comparison.py
```

### 10_customers/C101.csv

参数：`iterations=500, seed=42, max_total=3, max_per_satellite=2`

| 方案 | cost | 中转数 | 试探次数 | 内层步数 | 成功加入候选次数 |
|---|---:|---:|---:|---:|---:|
| Model 2 | 591.38 | 0 | - | - | - |
| Model 3 | 579.06 | 0 | - | - | - |
| Model 4 | 591.38 | 0 | - | - | - |
| Model 5, H=1 | 601.20 | 1 | 500 | 500 | 8 |
| Model 5, H=2 | 599.49 | 2 | 500 | 514 | 16 |
| Model 5, H=3 | 599.49 | 2 | 500 | 516 | 16 |
| Model 5, H=3 best-prefix | 599.49 | 2 | 500 | 516 | 16 |
| Model 5, H=3 decreasing | 599.49 | 2 | 500 | 516 | 16 |

### 10_customers/R101.csv

参数：`iterations=500, seed=42, max_total=3, max_per_satellite=2`

| 方案 | cost | 中转数 | 试探次数 | 内层步数 | 成功加入候选次数 |
|---|---:|---:|---:|---:|---:|
| Model 2 | 596.16 | 0 | - | - | - |
| Model 3 | 596.16 | 0 | - | - | - |
| Model 4 | 596.16 | 0 | - | - | - |
| Model 5, H=1 | 603.84 | 0 | 500 | 500 | 10 |
| Model 5, H=2 | 610.37 | 2 | 500 | 512 | 12 |
| Model 5, H=3 | 610.37 | 2 | 500 | 512 | 12 |
| Model 5, H=3 best-prefix | 610.37 | 2 | 500 | 512 | 12 |
| Model 5, H=3 decreasing | 610.37 | 2 | 500 | 506 | 12 |

### 10_customers/RC101.csv

参数：`iterations=500, seed=42, max_total=3, max_per_satellite=2`

| 方案 | cost | 中转数 | 试探次数 | 内层步数 | 成功加入候选次数 |
|---|---:|---:|---:|---:|---:|
| Model 2 | 665.22 | 0 | - | - | - |
| Model 3 | 654.84 | 0 | - | - | - |
| Model 4 | 665.22 | 0 | - | - | - |
| Model 5, H=1 | 635.35 | 0 | 500 | 500 | 7 |
| Model 5, H=2 | 662.48 | 2 | 500 | 506 | 6 |
| Model 5, H=3 | 662.48 | 2 | 500 | 506 | 6 |
| Model 5, H=3 best-prefix | 662.48 | 2 | 500 | 506 | 6 |
| Model 5, H=3 decreasing | 662.48 | 2 | 500 | 506 | 6 |

### 50_customers/C101.csv

参数：`iterations=300, seed=42, max_total=8, max_per_satellite=3`

| 方案 | cost | 中转数 | 试探次数 | 内层步数 | 成功加入候选次数 |
|---|---:|---:|---:|---:|---:|
| Model 2 | 1389.50 | 0 | - | - | - |
| Model 3 | 1458.49 | 6 | - | - | - |
| Model 4 | 1389.50 | 0 | - | - | - |
| Model 5, H=1 | 1518.29 | 8 | 300 | 300 | 18 |
| Model 5, H=2 | 1616.38 | 8 | 300 | 320 | 25 |
| Model 5, H=3 | 1686.79 | 8 | 300 | 310 | 11 |
| Model 5, H=3 best-prefix | 1686.79 | 8 | 300 | 310 | 11 |
| Model 5, H=3 decreasing | 1686.79 | 8 | 300 | 310 | 11 |

### 最新结论

1. 当前 Model 5 已符合“每个 ALNS candidate 内部做最多 H 次中转迭代，再判断是否进入下一轮”的结构。
2. 从结果看，内层中转迭代次数不是越多越好；`H=2/3` 只在 10 客户 C101 上略优于 `H=1`，但在 R101、RC101 和 50 客户 C101 上都更差。
3. 当前实现下，`H=1` 是更稳的主方案，`H=2` 可以作为敏感性分析；不建议把 `H=3` 作为主方案。
4. Model 5 在 10 客户 RC101 上优于 Model 2/3/4，说明嵌套中转机制有潜力；但在 C101/R101 和 50 客户 C101 上不稳定。
5. 下一步如果继续增强 Model 5，不应单纯增加 H，而应改进内层中转的候选筛选，例如收益区间、beam search 或中转 pruning。

## 19. 中转操作选择策略对比

根据最新讨论，进一步比较了每次中转迭代中“如何选择一个中转操作”的策略。

已实现策略：

| 策略 | 代码参数 | 含义 |
|---|---|---|
| 方案 A | `greedy` | 每次选择当前搜索到的最佳中转 |
| 方案 B | `range_random` | 生成候选池，选取收益达到 `alpha * best_gain` 的方案，并按收益加权随机选择 |
| 方案 C | `beam` | 保留若干个中转后候选解，做候选池/beam 搜索 |
| 方案 D | `conservative` | 只接受强收益中转，当前 `conservative_min_saving=1.0` |
| 方案 A 扩展 | `greedy, H=2` | 最佳中转连续执行最多 2 步 |

实验脚本：

```text
python3 experiments/run_model5_strategy_comparison.py
```

### 实验设置

10 客户算例：

```text
datasets = C101, C103, R101, R103, RC101, RC103
iterations = 300
seed = 42
max_total = 3
max_per_satellite = 2
```

50 客户算例：

```text
dataset = C101
iterations = 300
seed = 42
max_total = 8
max_per_satellite = 3
```

### 结果摘要

| 算例 | Model 2 | Model 3 | Model 4 | M5-A greedy H1 | M5-B range | M5-C beam | M5-D conservative | M5-A greedy H2 | 最优 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 10 C101 | 576.68 | **568.25** | 576.68 | 604.05 | 593.18 | 586.31 | 604.05 | 585.09 | Model 3 |
| 10 C103 | **566.50** | 588.15 | **566.50** | 601.76 | 590.64 | 592.39 | 580.76 | 583.30 | Model 2/4 |
| 10 R101 | 604.02 | **598.40** | 604.02 | 606.14 | 629.85 | 600.84 | 606.14 | 607.21 | Model 3 |
| 10 R103 | 546.10 | 546.72 | 546.10 | 546.72 | **546.03** | 546.49 | 546.72 | 546.72 | M5-B |
| 10 RC101 | 665.22 | 692.39 | 665.22 | **635.35** | 667.99 | 661.76 | **635.35** | 662.48 | M5-A/D |
| 10 RC103 | 683.38 | 757.77 | 683.38 | 683.38 | 685.57 | **674.87** | 683.38 | 683.23 | M5-C |
| 50 C101 | **1389.50** | 1458.49 | **1389.50** | 1518.29 | 1559.53 | 1567.66 | 1503.03 | 1616.38 | Model 2/4 |

输出文件：

```text
output/experiments/model5_strategies_10_customers_C101_iter300_seed42_summary.csv
output/experiments/model5_strategies_10_customers_C103_iter300_seed42_summary.csv
output/experiments/model5_strategies_10_customers_R101_iter300_seed42_summary.csv
output/experiments/model5_strategies_10_customers_R103_iter300_seed42_summary.csv
output/experiments/model5_strategies_10_customers_RC101_iter300_seed42_summary.csv
output/experiments/model5_strategies_10_customers_RC103_iter300_seed42_summary.csv
output/experiments/model5_strategies_50_customers_C101_iter300_seed42_summary.csv
```

### 策略结论

1. 没有一个 Model 5 策略在所有算例上都最好，说明中转选择策略对算例结构高度敏感。
2. Model 5 在 7 个有效算例中有 3 个算例取得全体最优：10 R103、10 RC101、10 RC103。
3. `range_random` 只在 10 R103 上最好，说明收益范围随机有一定探索价值，但稳定性不足。
4. `beam` 在 10 RC103 上最好，且在 R101 上接近 Model 3，说明候选池保留能缓解单步贪心，但计算成本更高。
5. `conservative` 在 50 C101 中是 Model 5 内部最好，说明大规模下需要更强的中转准入门槛。
6. `greedy H2` 只在 10 C101 上改善了 `greedy H1`，其他算例普遍没有优势，继续说明“增加中转次数”不是主方向。

当前推荐：

```text
主模型：Model 5-A greedy H1 或 Model 5-D conservative H1
对照策略：range_random、beam、greedy H2
论文结论：中转嵌入 ALNS 有潜力，但必须控制中转准入；候选池和随机策略能提升部分算例表现，但普适性仍需进一步参数调优。
```

## 20. Model 5 进一步优化实验

进一步围绕“中转是否值得继续做、Model 5 还有没有优化空间”新增了三类改进：

| 改进 | 含义 |
|---|---|
| 中转 pruning | 对已有中转逐个尝试撤销，若撤销后总成本下降，则删除该中转 |
| 质量门控 | 只有当 ALNS candidate 没有比当前解差太多时，才执行中转试探 |
| 最终精修 | Model 5 搜索完成后，对最优解再执行少量中转精修 |

对应参数：

```text
transshipment_prune
transshipment_probe_quality_gap
transshipment_final_refine
```

实验脚本：

```text
python3 experiments/run_model5_advanced_comparison.py
python3 experiments/build_model5_advanced_big_table.py
python3 experiments/build_model5_overall_best_table.py
```

### 高级改进大表

单元格格式：`目标值 / 中转数`。

| 算例 | Model 2 | Model 3 | Model 4 | M5 base greedy | M5 conservative | M5 greedy+prune | M5 cons+prune | M5 cons+prune+gate | M5 cons+prune+refine | 最优 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 10 C101 | 576.68 / 0 | **568.25 / 0** | 576.68 / 0 | 604.05 / 0 | 604.05 / 0 | 579.06 / 0 | 579.06 / 0 | 579.06 / 0 | 579.06 / 0 | Model 3 |
| 10 C103 | **566.50 / 0** | 588.15 / 0 | **566.50 / 0** | 601.76 / 2 | 580.76 / 1 | 631.39 / 2 | 631.39 / 2 | 583.80 / 2 | 631.39 / 2 | Model 2/4 |
| 10 R101 | 604.02 / 0 | **598.40 / 0** | 604.02 / 0 | 606.14 / 1 | 606.14 / 1 | 616.52 / 0 | 616.35 / 0 | 654.87 / 0 | 616.35 / 0 | Model 3 |
| 10 R103 | 546.10 / 0 | 546.72 / 1 | 546.10 / 0 | 546.72 / 1 | 546.72 / 1 | 546.89 / 0 | 546.89 / 0 | 548.30 / 0 | **545.08 / 1** | M5 refine |
| 10 RC101 | 665.22 / 0 | 692.39 / 2 | 665.22 / 0 | **635.35 / 0** | **635.35 / 0** | **635.35 / 0** | **635.35 / 0** | 687.83 / 0 | **635.35 / 0** | Model 5 |
| 10 RC103 | **683.38 / 0** | 757.77 / 0 | **683.38 / 0** | **683.38 / 0** | **683.38 / 0** | 750.91 / 0 | 750.91 / 0 | 753.19 / 0 | 750.91 / 0 | Model 2/4 |
| 50 C101 | **1389.50 / 0** | 1458.49 / 6 | **1389.50 / 0** | 1518.29 / 8 | 1503.03 / 6 | 1473.10 / 7 | 1498.58 / 6 | 1454.77 / 4 | 1498.58 / 6 | Model 2/4 |

### 与上一轮最佳 Model 5 对比

| 算例 | 上一轮最佳 M5 | 高级最佳 M5 | 变化 |
|---|---:|---:|---:|
| 10 C101 | 585.09 | 579.06 | 改进 6.03 |
| 10 C103 | 580.76 | 580.76 | 持平 |
| 10 R101 | 600.84 | 606.14 | 变差 5.30 |
| 10 R103 | 546.03 | 545.08 | 改进 0.95 |
| 10 RC101 | 635.35 | 635.35 | 持平 |
| 10 RC103 | 674.87 | 683.38 | 变差 8.51 |
| 50 C101 | 1503.03 | 1454.77 | 改进 48.26 |

输出文件：

```text
output/experiments/model5_advanced_big_table_iter300_seed42.md
output/experiments/model5_overall_best_table_iter300_seed42.md
```

### 判断

1. 中转是值得继续做的。Model 5 在多个 R/RC 类算例上能优于 Model 2/3/4，说明中转能改变搜索结构，不只是后处理补丁。
2. 中转不是普适收益。C 类算例和 50 客户 C101 中，Model 2/4 仍明显更稳，说明中转容易破坏已经很规整的路线结构。
3. 进一步优化空间存在，但重点不是“更多中转”，而是“更会控制中转”。
4. pruning 对 50 客户 C101 有明显帮助，但在部分 10 客户算例上反而变差，说明撤销策略需要更精细，不能简单逐个撤销。
5. 质量门控在 50 客户上有效，使 Model 5 从 1503.03 降到 1454.77，并略优于 Model 3；但在小规模 R/RC 上不稳定。
6. 最终精修在 10 R103 上有效，把 546.03 进一步降到 545.08，但不是普适增强。

### 下一步最值得做

```text
1. 把中转收益从“局部距离节省”改成带惩罚的收益：
   adjusted_gain = distance_saving - lambda * transshipment_count

2. 对不同客户分布使用不同策略：
   C 类：更保守，甚至少中转；
   R/RC 类：允许 range_random 或 beam；
   大规模：启用 quality_gate + conservative。

3. 设计中转贡献评分，不只看撤销单个中转后的即时成本，
   而是结合其对后续 ALNS 搜索的影响。

4. 做多 seed 汇总平均值和标准差，避免单 seed 误判。
```

## 21. 针对 Model 2 的保护型优化

由于主对比目标是优于 Model 2，新增了 `baseline_guard` 机制：

```text
1. 先运行一轮无中转 ALNS，得到基线解；
2. 以该基线解为起点运行 Model 5 中转嵌入搜索；
3. 如果中转搜索后的结果差于基线解，则回退到基线解。
```

该机制的作用是：

```text
Model 5 最终输出不应比内部无中转基线更差。
```

新增参数：

```text
transshipment_baseline_guard
transshipment_baseline_iterations
transshipment_baseline_time
```

进一步实验发现，单纯 `guard` 可以保护 C 类算例，但会压制 RC101 这类自由 Model 5 的大幅改进。因此更合理的使用方式是 **Model 5 portfolio**：

```text
同时保留：
1. 自由搜索型 Model 5，用于发现新路线结构；
2. 基线保护型 Model 5，用于保证不差于 Model 2；
最后取两者中更好的结果。
```

### Model 2 vs 最佳 Model 5

实验设置：

```text
iterations = 300
seed = 42
非北京算例
```

| 算例 | Model 2 | 最佳 Model 5 | M5 策略 | gap | 结论 |
|---|---:|---:|---|---:|---|
| 10 C101 | 576.68 / 0 | 576.68 / 0 | guard greedy | 0.00 | 持平 |
| 10 C103 | 566.50 / 0 | 566.50 / 0 | guard greedy | 0.00 | 持平 |
| 10 R101 | 604.02 / 0 | 600.84 / 1 | beam H2 | -3.18 | 更好 |
| 10 R103 | 546.10 / 0 | 545.08 / 1 | cons prune refine | -1.02 | 更好 |
| 10 RC101 | 665.22 / 0 | 635.35 / 0 | greedy H1 | -29.87 | 更好 |
| 10 RC103 | 683.38 / 0 | 674.87 / 1 | beam H2 | -8.51 | 更好 |
| 50 C101 | 1389.50 / 0 | 1389.50 / 0 | guard greedy | 0.00 | 持平 |

输出文件：

```text
output/experiments/model2_vs_best_model5_iter300_seed42.md
output/experiments/model2_vs_best_model5_iter300_seed42.csv
```

结论：

```text
在当前 7 个非北京算例上，
最佳 Model 5 相对 Model 2：
4 个更好，3 个持平，0 个更差。
```

这说明中转机制是值得继续做的，但实际落地时应采用 **自由搜索 + 基线保护** 的组合，而不是单一固定中转策略。

## 22. 扩大算例后的普适性实验

为检验 Model 5 portfolio 相对 Model 2 是否具有一定普适性，进一步扩大实验规模：

```text
10_customers: 全部 30 个算例
50_customers: C 类 10 个算例
总计: 40 个非北京算例
iterations = 300
seed = 42
```

Model 5 portfolio 包含 4 个策略：

| 策略 | 含义 |
|---|---|
| `m5_greedy_H1` | 自由搜索，单步贪心中转 |
| `m5_beam_H2` | 候选池 beam，中转深度 2 |
| `m5_cons_prune_refine` | 保守准入 + pruning + 最终精修 |
| `m5_guard_greedy` | 基线保护 + 贪心中转 |

实验脚本：

```text
python3 experiments/run_model5_portfolio_comparison.py
```

输出文件：

```text
output/experiments/model5_portfolio_all10_iter300_seed42_summary.md
output/experiments/model5_portfolio_10datasets_iter300_seed42_summary.md
output/experiments/model5_portfolio_40datasets_iter300_seed42_summary.md
```

### 汇总结果

| 分组 | 算例数 | Model 5 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| 10_customers 全部 | 30 | 14 | 16 | 0 | -1.22% |
| 50_customers C类 | 10 | 4 | 6 | 0 | -0.79% |
| 合计 | 40 | 18 | 22 | 0 | -1.12% |

其中：

```text
gap = Best Model 5 - Model 2
gap < 0 表示 Model 5 优于 Model 2
```

### 重要观察

1. 扩大到 40 个算例后，Model 5 portfolio 没有出现差于 Model 2 的结果。
2. 18/40 个算例优于 Model 2，说明中转嵌入搜索具有一定普适收益，不只是个别算例偶然有效。
3. 22/40 个算例与 Model 2 持平，主要由 `m5_guard_greedy` 提供兜底，说明基线保护机制有效。
4. 10 客户算例平均改进幅度略高于 50 客户 C 类，说明小规模/混合结构中更容易找到有价值中转。
5. 50 客户 C 类仍有 4/10 个算例优于 Model 2，说明即使在较规整的 C 类大一点规模上，中转也不是完全无效。

### 对论文写法的启发

可以把 Model 5 定义为：

```text
基于中转嵌入搜索与基线保护的组合式 ALNS
```

核心不是单个中转策略，而是：

```text
自由搜索负责寻找中转带来的结构性改进；
基线保护负责保证算法不会劣于无中转基准。
```

当前扩大实验支持这样的结论：

```text
在 40 个非北京测试算例中，Model 5 portfolio 相较 Model 2
实现 18 个改进、22 个持平、0 个劣化，平均目标值下降 1.12%。
```

## 23. Multi-start Model 5 Portfolio

在当前 portfolio 基础上继续优化，新增 **multi-start portfolio**：

```text
同一组 Model 5 策略使用多个随机种子启动；
每个算例最终取所有 seed、所有策略中的最好结果。
```

测试策略：

| 策略 | 含义 |
|---|---|
| `m5_greedy_H1` | 自由搜索，单步贪心中转 |
| `m5_beam_H2` | 候选池 beam，中转深度 2 |
| `m5_cons_prune_refine` | 保守准入 + pruning + 最终精修 |
| `m5_guard_greedy` | 基线保护 + 贪心中转 |

实验设置：

```text
datasets = data/10_customers 全部 30 个算例
seeds = 42, 7
iterations = 300
Model 2 baseline seed = 42
```

实验脚本：

```text
python3 experiments/run_model5_multistart_portfolio.py
```

输出文件：

```text
output/experiments/model5_multistart_all10_iter300_seeds42-7_summary.md
output/experiments/model5_multistart_all10_iter300_seeds42-7_summary.csv
```

### 与单 seed portfolio 对比

| 方案 | 算例数 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| 单 seed portfolio | 30 | 14 | 16 | 0 | -1.22% |
| multi-start portfolio | 30 | 17 | 13 | 0 | -1.69% |

multi-start 将 3 个原本持平/较弱的算例进一步转化为优于 Model 2，同时没有引入劣化。

### 明显改善的算例

| 算例 | 单 seed 最佳 | multi-start 最佳 | 改进 | 最佳策略 | seed |
|---|---:|---:|---:|---|---:|
| C201 | 590.95 | 589.24 | 1.71 | greedy H1 | 7 |
| C205 | 498.06 | 495.76 | 2.30 | beam H2 | 7 |
| C209 | 580.61 | 574.59 | 6.02 | guard greedy | 7 |
| R101 | 600.84 | 599.22 | 1.62 | beam H2 | 7 |
| R103 | 545.08 | 543.39 | 1.69 | cons prune refine | 7 |
| R105 | 674.16 | 649.33 | 24.83 | guard greedy | 7 |
| R107 | 739.01 | 712.56 | 26.45 | greedy H1 | 7 |
| R201 | 554.61 | 548.95 | 5.66 | guard greedy | 7 |
| R207 | 615.97 | 600.04 | 15.93 | guard greedy | 7 |
| RC103 | 674.87 | 673.88 | 0.99 | greedy H1 | 7 |
| RC105 | 650.53 | 644.10 | 6.43 | guard greedy | 7 |

### 判断

multi-start 是当前最稳的进一步优化方向之一：

```text
1. 它不改变中转模型定义；
2. 它能利用 ALNS 随机性寻找更好的路线-中转组合；
3. 配合 guard 策略后，不会劣于 Model 2 baseline；
4. 在 30 个 10客户算例上，胜率从 14/30 提升到 17/30。
```

论文中可以把它作为 Model 5 的增强版：

```text
Model 5-MS：多启动组合式中转嵌入 ALNS
```

## 24. 文献启发的最终路径精修

继续优化 Model 5 时，参考了含中转/transfer 的 ALNS 文献中常见的
embedded local search 思路：中转候选改变了物理访问位置和搬运节点后，
最终路线顺序仍可能存在局部改进空间。

实现了一个保守版本：

```text
route_local_refine = True

只在最终 best 解上执行；
每次移动同一路径内的一个真实任务节点；
不移动 TRANS_PICK / TRANS_DROP 虚拟搬运节点；
每个试探都重新 synchronize_transshipments；
每个试探都执行全局 is_feasible；
只接受真实 total_cost 下降的移动。
```

注意：初始实现曾把路径精修放在 baseline guard 之后、Model 5 搜索之前。
这会改变 ALNS 起点，导致 `m5_guard_route_refine` 在个别算例上反而不如
`m5_guard_greedy`。已经修正为：

```text
路径精修只作为最终后处理；
不改变 baseline guard 进入 Model 5 搜索时的起点；
若需要回退 baseline，也只精修 baseline 的副本后再比较。
```

新增参数：

| 参数 | 含义 | 当前实验值 |
|---|---|---:|
| `route_local_refine` | 是否启用最终路径精修 | True/False |
| `route_local_refine_rounds` | best-improvement 扫描轮数 | 2 |
| `route_local_refine_max_moves` | 最多接受移动数 | 20 |

新增组合：

| 策略 | 含义 |
|---|---|
| `m5_guard_route_refine` | baseline guard + greedy H1 + 最终路径顺序精修 |

### 修正后 40 算例结果

实验设置：

```text
datasets = data/10_customers 全部 30 个
         + data/50_customers C101/C103/.../C209 共 10 个
iterations = 300
seed = 42
max_total_transshipments = 3
max_transshipments_per_satellite = 2
```

输出文件：

```text
output/experiments/model5_portfolio_40datasets_iter300_seed42_summary.md
output/experiments/model5_portfolio_40datasets_iter300_seed42_detail.md
output/experiments/model5_portfolio_40datasets_iter300_seed42_summary.csv
```

汇总：

| 算例组 | 算例数 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| 10_customers | 30 | 14 | 16 | 0 | -1.26% |
| 50_customers C类 | 10 | 4 | 6 | 0 | -0.34% |
| 合计 | 40 | 18 | 22 | 0 | -1.03% |

策略成为 Best M5 的次数：

| 策略 | 次数 |
|---|---:|
| `m5_guard_greedy` | 24 |
| `m5_greedy_H1` | 8 |
| `m5_beam_H2` | 4 |
| `m5_cons_prune_refine` | 3 |
| `m5_guard_route_refine` | 1 |

`m5_guard_route_refine` 的直接额外收益集中在：

| 算例 | guard greedy | guard route refine | 额外改进 |
|---|---:|---:|---:|
| 10_customers_RC107 | 645.32 | 638.16 | 7.16 |

判断：

```text
路径精修值得保留，但不是主收益来源。
主收益仍来自多策略中转嵌入搜索 + baseline guard。
路径精修更适合作为最终后处理增强项，而不是改变 ALNS 搜索起点。
```

### 修正后 multi-start 结果

实验设置：

```text
datasets = data/10_customers 全部 30 个
seeds = 42, 7
iterations = 300
Model 2 baseline seed = 42
```

输出文件：

```text
output/experiments/model5_multistart_30datasets_iter300_seeds42-7_summary.md
output/experiments/model5_multistart_30datasets_iter300_seeds42-7_summary.csv
```

汇总：

| 方案 | 算例数 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| multi-start + route refine portfolio | 30 | 17 | 13 | 0 | -1.73% |

成为 Best M5 的策略次数：

| 策略 | 次数 |
|---|---:|
| `m5_guard_greedy` | 17 |
| `m5_greedy_H1` | 8 |
| `m5_beam_H2` | 3 |
| `m5_cons_prune_refine` | 1 |
| `m5_guard_route_refine` | 1 |

最大改善算例：

| 算例 | Model 2 | Best M5 | 策略 | seed | gap% |
|---|---:|---:|---|---:|---:|
| RC105 | 700.77 | 644.10 | guard greedy | 7 | -8.09% |
| C209 | 619.31 | 574.59 | guard greedy | 7 | -7.22% |
| R207 | 639.26 | 600.04 | guard greedy | 7 | -6.14% |
| C109 | 656.92 | 625.62 | greedy H1 | 42 | -4.76% |
| RC101 | 665.22 | 635.35 | greedy H1 | 42 | -4.49% |

最终建议：

```text
论文主推：Model 5-Portfolio / Model 5-MS。
模型五内部机制：每轮 ALNS candidate 执行受控中转试探。
工程默认：开启 baseline guard；可选开启最终 route_local_refine。
实验结论：中转值得做，但必须受控；多策略、多启动比单一中转算子更稳。
```

## 25. Cap Portfolio：中转上限组合搜索

继续优化时发现：固定 `max_total_transshipments = 3` 并不总是好。
在很多算例中，允许过多中转会把候选解带偏；反而 `T=1/S=1`
这种稀疏中转上限更容易保留净收益。

因此新增实验脚本：

```text
experiments/run_model5_cap_sweep.py
```

核心思路：

```text
不再固定一个中转上限；
同时测试多个 (max_total, max_per_satellite) 组合；
每个算例在所有中转策略、所有上限中取最优结果。
```

测试上限：

| 记号 | max_total | max_per_satellite | 含义 |
|---|---:|---:|---|
| T1/S1 | 1 | 1 | 极稀疏中转 |
| T2/S1 | 2 | 1 | 稀疏中转 |
| T3/S2 | 3 | 2 | 旧默认 |
| T5/S3 | 5 | 3 | 放宽中转 |

测试策略：

| 策略 | 含义 |
|---|---|
| `greedy_H1` | 自由贪心单步中转 |
| `beam_H2` | beam 中转深度 2 |
| `cons_prune_refine` | 保守准入 + pruning + 最终中转精修 |
| `guard_greedy` | baseline guard + greedy H1 |
| `guard_route_refine` | baseline guard + greedy H1 + 路径精修 |

### Cap sweep 结果

实验设置：

```text
iterations = 300
seed = 42
caps = T1/S1, T2/S1, T3/S2, T5/S3
datasets = 40 个非北京算例
```

输出文件：

```text
output/experiments/model5_cap_sweep_40datasets_iter300_seed42_T1S1-T2S1-T3S2-T5S3_summary.md
output/experiments/model5_cap_sweep_40datasets_iter300_seed42_T1S1-T2S1-T3S2-T5S3_summary.csv
```

汇总：

| 方案 | 算例数 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| 旧 portfolio, T3/S2 | 40 | 18 | 22 | 0 | -1.03% |
| cap portfolio | 40 | 28 | 12 | 0 | -1.91% |

分组：

| 算例组 | 算例数 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| 10_customers | 30 | 19 | 11 | 0 | -1.81% |
| 50_customers C类 | 10 | 9 | 1 | 0 | -2.20% |

成为最佳的上限次数：

| 上限 | 次数 |
|---|---:|
| T1/S1 | 29 |
| T5/S3 | 5 |
| T3/S2 | 4 |
| T2/S1 | 2 |

成为最佳的策略次数：

| 策略 | 次数 |
|---|---:|
| `guard_greedy` | 15 |
| `greedy_H1` | 14 |
| `cons_prune_refine` | 6 |
| `beam_H2` | 4 |
| `guard_route_refine` | 1 |

相较旧 portfolio，新打开或明显改善的代表算例：

| 算例 | 旧 Best M5 | Cap Best M5 | 改进 | 策略/上限 |
|---|---:|---:|---:|---|
| 50_C203 | 1522.36 | 1450.55 | 71.81 | cons_prune_refine T5/S3 |
| 50_C107 | 1453.30 | 1382.29 | 71.01 | guard_greedy T2/S1 |
| 50_C109 | 1315.44 | 1282.04 | 33.40 | cons_prune_refine T1/S1 |
| 50_C209 | 1394.47 | 1368.40 | 26.07 | cons_prune_refine T1/S1 |
| R105 | 674.16 | 649.69 | 24.47 | cons_prune_refine T5/S3 |
| C209 | 580.61 | 561.48 | 19.13 | beam_H2 T1/S1 |
| C205 | 498.06 | 488.19 | 9.87 | greedy_H1 T1/S1 |
| R201 | 554.61 | 546.19 | 8.42 | greedy_H1 T5/S3 |

关键判断：

```text
中转不是越多越好。
多数算例最佳上限是 T1/S1，说明“少量高质量中转”比“允许累积中转”更稳。
但 T5/S3 在少数算例仍有明显收益，所以不应只固定 T1/S1。
最佳做法是 cap portfolio：稀疏上限为主，放宽上限作为补充。
```

## 26. Cap-MS：Cap portfolio + targeted multi-start

在 cap portfolio 的 12 个持平算例上，补充 seed=7 的 cap sweep。
统一评价时仍使用 seed=42 的 Model 2 基线。

输出文件：

```text
output/experiments/model5_cap_ms_40datasets_iter300_seeds42-7_targeted_summary.md
output/experiments/model5_cap_ms_40datasets_iter300_seeds42-7_targeted_summary.csv
```

汇总：

| 方案 | 算例数 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| cap portfolio | 40 | 28 | 12 | 0 | -1.91% |
| cap-MS targeted | 40 | 30 | 10 | 0 | -2.06% |

seed=7 额外打开的算例：

| 算例 | Model 2 | seed42 cap best | seed7 cap best | gap% |
|---|---:|---:|---:|---:|
| R107 | 739.01 | 739.01 | 712.56 | -3.58% |
| RC208 | 558.00 | 558.00 | 543.39 | -2.62% |

当前最推荐的模型五版本：

```text
Model 5-Cap-MS：
每轮 ALNS candidate 中转试探
+ 多策略中转搜索
+ 多中转上限 portfolio
+ baseline guard
+ 可选最终 route_local_refine
+ 对持平算例做 targeted multi-start
```

论文表达可以写为：

```text
模型五不是简单地增加中转次数，而是通过中转上限组合控制中转稀疏性。
实验表明，稀疏中转上限 T1/S1 在多数算例中最有效，
说明高质量少量中转优于无约束的中转累积。
```

## 27. Targeted seed99 与 nearest_k 候选模式

继续根据文献中的两个启发做小范围优化：

```text
1. ALNS/GRASP 类方法常用多启动增强鲁棒性；
2. transfer/transshipment 插入候选需要控制，不能无限随机扩张。
```

因此在 Cap-MS 后，对仍持平的算例做两类补充：

```text
targeted seed=99 cap sweep
targeted nearest_k candidate mode cap sweep
```

新增接口：

```text
experiments/run_model345_comparison.py
  --transshipment-candidate-mode {all_sampled, nearest_k, all_exhaustive}
  --max-relocation-candidates K

experiments/run_model5_cap_sweep.py
  同步支持上述参数
```

### seed=99 targeted 结果

在 Cap-MS 仍持平的 10 个算例上补充 seed=99。
seed=99 单独不是整体更稳，但作为候选池可以打开 3 个原持平点。

输出文件：

```text
output/experiments/model5_cap_ms_40datasets_iter300_seeds42-7-99_targeted_summary.md
output/experiments/model5_cap_ms_40datasets_iter300_seeds42-7-99_targeted_summary.csv
```

汇总：

| 方案 | 算例数 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Cap-MS seeds42-7 | 40 | 30 | 10 | 0 | -2.06% |
| Cap-MS seeds42-7-99 targeted | 40 | 33 | 7 | 0 | -2.14% |

seed=99 额外打开：

| 算例 | Model 2 | 新 Best M5 | 策略/上限 | gap% |
|---|---:|---:|---|---:|
| RC201 | 578.47 | 574.59 | cons_prune_refine T3/S2 | -0.67% |
| RC203 | 599.20 | 587.75 | guard_greedy T1/S1 | -1.91% |
| 50_C101 | 1389.50 | 1385.25 | guard_greedy T1/S1 | -0.31% |

### nearest_k targeted 结果

在 seed42/7/99 后仍持平的 7 个算例上，补充：

```text
transshipment_candidate_mode = nearest_k
max_relocation_candidates = 3
```

输出文件：

```text
output/experiments/model5_cap_ms_nearest_40datasets_iter300_summary.md
output/experiments/model5_cap_ms_nearest_40datasets_iter300_summary.csv
```

汇总：

| 方案 | 算例数 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Cap-MS seeds42-7-99 targeted | 40 | 33 | 7 | 0 | -2.14% |
| Cap-MS + nearest_k targeted | 40 | 34 | 6 | 0 | -2.15% |

nearest_k 额外打开：

| 算例 | Model 2 | 新 Best M5 | 策略/上限 | gap% |
|---|---:|---:|---|---:|
| C105 | 515.52 | 512.24 | cons_prune_refine T5/S3 | -0.64% |

仍持平的 6 个算例：

```text
C107, C203, C207, R203, R209, RC108
```

判断：

```text
targeted multi-start 有继续收益；
nearest_k 候选模式有小幅收益，但边际收益明显低于 cap portfolio；
剩余 6 个持平算例可能不是参数问题，而是当前中转邻域本身找不到净收益。
```

若继续优化，下一步不建议继续盲目加 seed，而应考虑改变邻域定义：

```text
1. 允许一次中转操作评估多个 pickup 的组合收益；
2. 在最终解上做跨路线 relocate/exchange，而不是仅同一路径顺序精修；
3. 建立 elite route pool，再做路线重组或集合划分式选择。
```

## 28. 更大规模算例实验

为了检查 Model 5 是否只在小算例上有效，新增紧凑版大规模组合实验脚本：

```text
experiments/run_model5_large_portfolio.py
```

该脚本不跑完整 20 组合 cap sweep，而是选取前面实验中最有效的代表配置：

| 策略 | 含义 |
|---|---|
| `sparse_greedy_T1S1` | T1/S1 稀疏贪心中转 |
| `sparse_beam_T1S1` | T1/S1 稀疏 beam |
| `sparse_cons_T1S1` | T1/S1 保守 + pruning + refine |
| `sparse_guard_T1S1` | T1/S1 + baseline guard |
| `mid_guard_T2S1` | T2/S1 + baseline guard |
| `default_greedy_T3S2` | 旧默认 T3/S2 |
| `wide_cons_T5S3` | T5/S3 保守 + pruning + refine |
| `wide_guard_T5S3` | T5/S3 + baseline guard |
| `nearest_cons_T5S3` | nearest_k 候选 + T5/S3 保守中转 |

实验设置：

```text
iterations = 200
seed = 42
time = 30
```

输出文件：

```text
output/experiments/model5_large_portfolio_100C_10datasets_iter200_seed42_summary.md
output/experiments/model5_large_portfolio_50RRC_20datasets_iter200_seed42_summary.md
output/experiments/model5_large_portfolio_100RRC_12datasets_iter200_seed42_summary.md
output/experiments/model5_large_scale_combined_summary.md
```

### 大规模分组结果

| 组别 | 总数 | 可比 | 更好 | 持平 | 更差 | 失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|---:|
| 50C full cap | 10 | 10 | 9 | 1 | 0 | 0 | -2.20% |
| 50RRC compact | 20 | 13 | 12 | 1 | 0 | 7 | -4.85% |
| 100C compact | 10 | 10 | 10 | 0 | 0 | 0 | -4.60% |
| 100RRC compact | 12 | 7 | 7 | 0 | 0 | 5 | -6.51% |
| 合计 | 52 | 40 | 38 | 2 | 0 | 12 | -4.42% |

说明：

```text
failed 表示 Model 2 和 Model 5 当前都没有得到可比可行解。
这些失败集中在 50/100 的 R/RC 类，可能与当前车队配置和初始解构造有关。
在可比算例中，Model 5 没有出现劣于 Model 2 的结果。
```

### 大规模代表性改进

| 组别 | 算例 | Model 2 | Best M5 | 策略 | gap% |
|---|---|---:|---:|---|---:|
| 100C | C105 | 2244.96 | 2029.25 | sparse_greedy_T1S1 | -9.61% |
| 100RRC | R209 | 2704.40 | 2481.29 | sparse_guard_T1S1 | -8.25% |
| 100RRC | RC201 | 3037.44 | 2795.90 | sparse_greedy_T1S1 | -7.95% |
| 50RRC | RC103 | 1635.11 | 1507.48 | mid_guard_T2S1 | -7.81% |
| 50RRC | RC107 | 1686.01 | 1558.02 | sparse_cons_T1S1 | -7.59% |
| 100RRC | R207 | 2600.96 | 2407.06 | sparse_guard_T1S1 | -7.45% |
| 50RRC | R207 | 1608.59 | 1492.59 | sparse_beam_T1S1 | -7.21% |
| 50C | C203 | 1544.90 | 1450.55 | cons_prune_refine_T5S3 | -6.11% |
| 100C | C207 | 2314.62 | 2185.48 | wide_cons_T5S3 | -5.58% |

### 大规模结论

```text
1. Model 5-Cap/compact portfolio 在更大规模上没有失效，反而平均改进更大。
2. 100C 的 10 个算例全部优于 Model 2，平均下降 4.60%。
3. 100RRC 中可比的 7 个算例全部优于 Model 2，平均下降 6.51%。
4. sparse_guard_T1S1 是大规模最常见赢家，说明稀疏中转 + guard 是稳健核心。
5. T5/S3 仍在少数算例显著有效，因此 portfolio 不应只保留 T1/S1。
6. R/RC 大规模失败项应单独作为“可行初始解构造/车队配置”问题处理。
```

下一步优化方向：

```text
优先级 1：改进大规模 R/RC 的初始解构造，使更多算例进入可比集合。
优先级 2：加入跨路线 relocate/exchange 最终精修，针对仍持平或失败边缘算例。
优先级 3：考虑 elite route pool / route recombination，而不是继续盲目增加中转次数。
```

## 29. Model 5 强可行初始解实验

实现位置：

```text
models/strong_initial_solution.py
models/model_5/alns_solver.py
```

核心思路：

```text
旧初始解先把 E1 配送和回收都排好，再构造 E2，容易出现 E1 送达过晚、
E2 无法满足顾客时间窗的问题。

强初始解改为：
1. 先构造 E1 卫星配送路线；
2. 根据 E1 卫星送达时间构造 E2 顾客取送货路线；
3. 根据 E2 返回时间再插入 E1 卫星回收任务；
4. 对 E1 配送同时尝试“成本型组合路线”和“紧迫卫星单独派车路线”，
   选择全局可行且成本最低的初始解。
```

实验设置：

```text
iterations = 80
time = 10 per solver/variant
seed = 42
transshipment_time_budget = 0.03
Model 5 compact portfolio = 9 个中转配置
```

### 原失败集恢复效果

输出文件：

```text
output/experiments/model5_large_portfolio_12datasets_strong_initial_iter80_seed42_summary.md
output/experiments/model5_large_portfolio_12datasets_legacy_initial_iter80_seed42_summary.md
```

| 初始解 | 原失败算例数 | M5 恢复可行 | 仍均无解 |
|---|---:|---:|---:|
| legacy initial | 12 | 0 | 12 |
| strong initial v1 | 12 | 7 | 5 |
| strong initial v2 | 12 | 11 | 1 |

剩余无解算例：

```text
50_customers_RC101
原因是当前固定卫星分配下存在硬时间窗冲突：
D0 -> S0 = 127.48，S0 -> C65 = 20.00，
C65 配送截止 = 145.00，因此最早到达 C65 = 147.48 > 145.00。
这不是 ALNS 或中转失败，而是当前预分配/时间窗组合不可行。
```

### 50C 可比算例效果

输出文件：

```text
output/experiments/model5_large_portfolio_10datasets_strong_initial_iter80_seed42_summary.md
output/experiments/model5_large_portfolio_10datasets_legacy_initial_iter80_seed42_summary.md
```

| 对比 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|
| M5 legacy initial vs Model 2 | 10 | 0 | 0 | -3.77% |
| M5 strong initial vs Model 2 | 10 | 0 | 0 | -7.22% |
| M5 strong initial vs legacy initial | 10 | 0 | 0 | 平均成本 -52.68 |

结论：

```text
强可行初始解是 Model 5 的有效优化方向：
1. 显著减少 R/RC 大规模算例的“未得到可行解”；
2. 在 Model 2 已可行的 50C 算例上也进一步降低最终成本；
3. 继续堆叠中转次数之前，应优先保证 E1 送达时间和 E2 时间窗兼容。
```

## 30. Model 2/3/4/5 全部非北京算例全量对比（strong compact 历史结果）

实验设置：

```text
数据：data/5_customers、10_customers、50_customers、100_customers
排除：data/beijing_fresh
算例数：120
iterations = 80
time = 10 per solver/variant
seed = 42
transshipment_time_budget = 0.03
Model 5 = strong initial + compact portfolio 9 个配置取最优
```

输出文件：

```text
output/experiments/model2345_all_nonbeijing_120datasets_iter80_seed42.md
output/experiments/model2345_all_nonbeijing_120datasets_iter80_seed42.csv
```

### 总体结果

| 模型 | 可行数 | 失败数 | 相对 M2 更好 | 持平 | 更差 | M2 无解但本模型可行 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|---:|
| M2 | 102 | 18 | 0 | 102 | 0 | 0 | 0.00% |
| M3 | 102 | 18 | 17 | 22 | 63 | 0 | 3.57% |
| M4 | 102 | 18 | 24 | 78 | 0 | 0 | -0.05% |
| M5 | 118 | 2 | 78 | 12 | 12 | 16 | -6.96% |

最佳模型次数：

```text
M5: 93
M2: 21
M3: 3
M4: 1
none: 2
```

### 分规模结果

| 规模 | M5 可行数 | M5 更好 | M5 持平 | M5 更差 | M5 恢复可行 | M5 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|
| 5_customers | 30/30 | 16 | 12 | 2 | 0 | -6.19% |
| 10_customers | 30/30 | 21 | 0 | 9 | 0 | -3.34% |
| 50_customers | 29/30 | 22 | 0 | 1 | 6 | -7.24% |
| 100_customers | 29/30 | 19 | 0 | 0 | 10 | -13.55% |

M5 仍失败算例：

```text
100_customers_R101
50_customers_RC101
```

说明：

```text
1. Model 5 当前优势非常明显，尤其是 50/100 客户规模。
2. Model 5 比 Model 2 多恢复 16 个可行解，这主要来自 strong initial。
3. Model 5 的 12 个 worse 主要集中在 5/10 小规模算例，以及 50_customers_RC107。
4. 小规模算例中，中转/强初始解未必总能抵消额外路径结构扰动，需要 baseline guard 或 no-transshipment fallback 做更强保护。
```

## 31. Model 5 更差情况修复：Hybrid Safety

背景：

```text
120 个非北京全量实验中，strong initial + compact portfolio 的 Model 5
虽然整体最好，但出现 12 个相对 Model 2 更差的算例。
这些 worse 主要集中在 5/10 小规模，以及 50_customers_RC107。
```

### worse12 targeted 实验

12 个 worse 算例：

```text
10_customers_C109
10_customers_C203
10_customers_R105
10_customers_R109
10_customers_R201
10_customers_R207
10_customers_RC105
10_customers_RC203
10_customers_RC207
50_customers_RC107
5_customers_RC103
5_customers_RC108
```

测试方法：

```text
1. strong initial seed=7
2. strong initial seed=99
3. legacy initial + guard safety
4. strong + legacy hybrid safety
5. hybrid safety + route refine 分支
```

结果：

| 方法 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|
| strong seed42 当前结果 | 0 | 0 | 12 | 4.36% |
| strong seed7 | 1 | 0 | 11 | 3.98% |
| strong seed99 | 1 | 0 | 11 | 3.81% |
| legacy safety only | 6 | 6 | 0 | -2.09% |
| strong + legacy hybrid safety | 6 | 6 | 0 | -2.09% |
| hybrid safety + route refine 分支 | 6 | 6 | 0 | -2.09% |

输出文件：

```text
output/experiments/model5_large_portfolio_12datasets_legacy_initial_worse12_legacy_iter80_seed42_summary.md
output/experiments/model5_large_portfolio_12datasets_strong_initial_worse12_seed7_iter80_seed7_summary.md
output/experiments/model5_large_portfolio_12datasets_strong_initial_worse12_seed99_iter80_seed99_summary.md
output/experiments/model5_large_portfolio_12datasets_strong_initial_hybrid_safety_worse12_hybrid_refine_iter80_seed42_summary.md
```

### 全量结果实测

重新运行 120 个非北京算例，Model 5 默认使用 Hybrid-Safety portfolio：

| 方法 | 可行数 | 失败数 | 更好 | 持平 | 更差 | 恢复M2无解 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|---:|
| 当前 strong compact | 118 | 2 | 78 | 12 | 12 | 16 | -6.96% |
| M5-Hybrid-Safety 实测 | 118 | 2 | 84 | 18 | 0 | 16 | -7.84% |
| 外层 Model2 fallback 估计 | 118 | 2 | 78 | 24 | 0 | 16 | -7.47% |

完整输出：

```text
output/experiments/model2345_all_nonbeijing_120datasets_hybrid_safety_iter80_seed42.md
output/experiments/model2345_all_nonbeijing_120datasets_hybrid_safety_iter80_seed42.csv
```

分规模 Model 5 实测：

| 规模 | M5 可行数 | 更好 | 持平 | 更差 | 恢复可行 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|
| 5_customers | 30/30 | 16 | 14 | 0 | 0 | -6.47% |
| 10_customers | 30/30 | 26 | 4 | 0 | 0 | -5.44% |
| 50_customers | 29/30 | 23 | 0 | 0 | 6 | -7.99% |
| 100_customers | 29/30 | 19 | 0 | 0 | 10 | -13.58% |

工程实现：

```text
experiments/run_model5_large_portfolio.py
  BASE_COMPACT_VARIANTS + LEGACY_SAFETY_VARIANTS

experiments/run_model2345_all_nonbeijing.py
  每个 variant 支持独立 strong_initial 设置
```

结论：

```text
1. 单纯多 seed 不能稳定解决 worse。
2. 更差主要来自 strong initial 在小规模上改变搜索起点，导致内部 guard 保护不到 Model 2 旧基线。
3. legacy initial + baseline guard 是最有效的安全分支。
4. 推荐版本为 Model 5-Hybrid-Safety：
   strong initial 负责恢复可行性和大规模改进；
   legacy safety 负责小规模防劣化。
```

## 32. 统一强初始解公平对比

动机：

```text
此前 Model 5 默认使用 strong initial，而 Model 2/3/4 使用 legacy initial。
为拆分“强初始解贡献”和“模型五中转机制贡献”，给 Model 2/3/4
也加入可选 strong_initial_solution 开关，并重新运行 120 个非北京算例。
```

工程改动：

```text
models/model_2/alns_solver.py
models/model_3/alns_solver.py
models/model_4/alns_solver.py
  新增 strong_initial_solution=False 参数。

experiments/run_model345_comparison.py
experiments/run_model2345_all_nonbeijing.py
  新增 --strong-initial-baselines。
```

实验设置：

```text
数据：data/5_customers、10_customers、50_customers、100_customers
排除：data/beijing_fresh
算例数：120
iterations = 80
time = 10 per solver/variant
seed = 42
Model 2/3/4 = strong initial
Model 5 = Hybrid-Safety portfolio
```

输出：

```text
output/experiments/model2345_all_nonbeijing_120datasets_hybrid_safety_iter80_seed42_strong_baselines.md
output/experiments/model2345_all_nonbeijing_120datasets_hybrid_safety_iter80_seed42_strong_baselines.csv
```

总体结果：

| 模型 | 可行数 | 失败数 | 比强M2更好 | 持平 | 更差 | M2无解但可行 | 平均 gap% |
|---|---:|---:|---:|---:|---:|---:|---:|
| Model 2 + strong initial | 118 | 2 | 0 | 118 | 0 | 0 | 0.00% |
| Model 3 + strong initial | 118 | 2 | 23 | 47 | 48 | 0 | 0.82% |
| Model 4 + strong initial | 118 | 2 | 13 | 105 | 0 | 0 | -0.01% |
| Model 5-Hybrid-Safety | 118 | 2 | 82 | 36 | 0 | 0 | -2.53% |

分规模 Model 5：

| 规模 | M5 可行数 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| 5_customers | 30/30 | 5 | 25 | 0 | -0.72% |
| 10_customers | 30/30 | 21 | 9 | 0 | -3.40% |
| 50_customers | 29/30 | 28 | 1 | 0 | -3.03% |
| 100_customers | 29/30 | 28 | 1 | 0 | -3.01% |

结论：

```text
1. 是的，之前主对比中只有 Model 5 使用 strong initial。
2. strong initial 本身非常有效：Model 2 可行数从 102/120 提升到 118/120。
3. 统一强初始解后，Model 5 的平均优势从 -7.84% 缩小到 -2.53%，
   说明原优势中一部分确实来自初始解。
4. 但 Model 5 仍然 82 更好、36 持平、0 更差，
   说明迭代中转 + Hybrid-Safety portfolio 仍有独立有效性。
```

## 33. 优化补充分支：risk-aware / exhaustive / prefix beam

动机：

```text
Hybrid-Safety 已经解决 worse 问题，但仍可能在部分算例上被当前候选池限制。
继续尝试不改变主框架，只给 portfolio 增加少量补充分支。
```

新增分支：

| 分支 | 含义 |
|---|---|
| `risk_sparse_guard_T1S1` | 稀疏中转 + baseline guard + 时间/容量余量评分 |
| `risk_nearest_guard_T5S3` | nearest_k 候选 + 较宽中转上限 + 风险感知评分 |
| `exhaustive_sparse_guard_T1S1` | T1/S1 稀疏约束下更完整枚举候选 |
| `prefix_beam_T2S1` | 最多两步中转，用 best_prefix 保留最好前缀 |

小规模结果：

```text
数据：5_customers + 10_customers，共 60 个非北京算例
iterations = 60
time = 6
Model 2/3 = strong initial
```

| 对比 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|
| Model 3 vs Model 2 | 5 | 44 | 11 | 0.67% |
| Current M5 vs Model 2 | 26 | 34 | 0 | -2.10% |
| Optimized M5 vs Model 2 | 27 | 33 | 0 | -2.18% |
| Optimized M5 vs Current M5 | 2 | 58 | 0 | -0.08% |

大规模代表样本结果：

```text
数据：50/100_customers 代表样本 24 个，覆盖 C/R/RC
iterations = 40
time = 5
Model 2/3 = strong initial
```

| 对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Model 3 vs Model 2 | 11 | 1 | 10 | 2 | -0.25% |
| Current M5 vs Model 2 | 22 | 0 | 0 | 2 | -3.45% |
| Optimized M5 vs Model 2 | 22 | 0 | 0 | 2 | -3.88% |
| Optimized M5 vs Current M5 | 4 | 18 | 0 | 2 | -0.43% |

新增分支实际贡献的算例：

| 算例 | Current M5 | Optimized M5 | 胜出分支 |
|---|---:|---:|---|
| 5_customers_RC208 | 350.08 | 343.80 | `prefix_beam_T2S1` |
| 10_customers_C109 | 656.92 | 638.71 | `risk_nearest_guard_T5S3` |
| 50_customers_R201 | 1502.86 | 1488.04 | `exhaustive_sparse_guard_T1S1` |
| 50_customers_RC201 | 1645.87 | 1559.67 | `exhaustive_sparse_guard_T1S1` |
| 50_customers_R107 | 1884.19 | 1878.73 | `prefix_beam_T2S1` |
| 100_customers_RC108 | 2478.01 | 2402.48 | `risk_nearest_guard_T5S3` |

结论：

```text
1. 新增分支不适合替代 Hybrid-Safety 主配置。
2. 作为 portfolio 补充有效：小规模和大规模均 0 worse。
3. 大规模 R/RC 上收益更明显，尤其 exhaustive_sparse 和 risk_nearest。
4. 当时推荐后续版本命名为 Model 5-Hybrid-Safety-Optimized；
   当前推荐已在第 36 节更新为 Optimized-V5。
```

## 34. V2 补充分支：停滞增强与风险精修

动机：

```text
V1 分支已经能补足部分 current M5 的盲区，但仍有少量算例停在局部结构。
因此继续尝试两类增强：
1. 搜索停滞后临时提高中转试探深度和候选强度；
2. 在风险感知分支上叠加 prune、final refine 和 route local refine。
```

新增工程参数：

```text
transshipment_stagnation_boost
transshipment_stagnation_window
transshipment_stagnation_depth
transshipment_stagnation_time_factor
transshipment_stagnation_candidate_mode
transshipment_stagnation_max_relocation_candidates
transshipment_stagnation_position_top_k
```

新增 V2 分支：

| 分支 | 含义 |
|---|---|
| `stagnation_exhaustive_T1S1` | 停滞后在 T1/S1 下更完整枚举 |
| `stagnation_nearest_T5S3` | 停滞后扩大 nearest_k 候选与插入位置 |
| `stagnation_prefix_beam_T3S1` | 停滞后将 best-prefix beam 深度提高到 3 |
| `risk_refine_guard_T5S3` | 风险感知 + conservative + prune/final refine/route refine |

小规模结果：

```text
数据：5_customers + 10_customers，共 60 个非北京算例
iterations = 60
time = 6
Model 2/3 = strong initial
```

| 对比 | 更好 | 持平 | 更差 | 平均 gap% |
|---|---:|---:|---:|---:|
| Current M5 vs Model 2 | 26 | 34 | 0 | -2.10% |
| Optimized V1 vs Model 2 | 27 | 33 | 0 | -2.18% |
| Optimized V2 vs Model 2 | 31 | 29 | 0 | -2.35% |
| Optimized V2 vs V1 | 6 | 54 | 0 | -0.17% |

小规模 V2 新增改善：

| 算例 | V1 | V2 | 胜出分支 |
|---|---:|---:|---|
| 5_customers_C103 | 358.52 | 339.82 | `risk_refine_guard_T5S3` |
| 10_customers_C201 | 584.16 | 582.34 | `stagnation_exhaustive_T1S1` |
| 10_customers_R105 | 674.16 | 646.38 | `risk_refine_guard_T5S3` |
| 10_customers_RC103 | 624.04 | 623.72 | `risk_refine_guard_T5S3` |
| 10_customers_RC107 | 619.73 | 619.37 | `risk_refine_guard_T5S3` |
| 10_customers_RC205 | 630.21 | 627.29 | `risk_refine_guard_T5S3` |

大规模代表样本结果：

```text
数据：50/100_customers 代表样本 24 个
iterations = 40
time = 5
Model 2/3 = strong initial
```

| 对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Current M5 vs Model 2 | 22 | 0 | 0 | 2 | -3.50% |
| Optimized V1 vs Model 2 | 22 | 0 | 0 | 2 | -3.88% |
| Optimized V2 vs Model 2 | 22 | 0 | 0 | 2 | -3.90% |
| Optimized V2 vs V1 | 3 | 19 | 0 | 2 | -0.02% |

大规模 V2 新增改善：

| 算例 | V1 | V2 | 胜出分支 |
|---|---:|---:|---|
| 50_customers_C209 | 1438.76 | 1438.64 | `risk_refine_guard_T5S3` |
| 50_customers_R107 | 1878.73 | 1873.97 | `stagnation_prefix_beam_T3S1` |
| 100_customers_RC108 | 2402.48 | 2400.63 | `stagnation_nearest_T5S3` |

结论：

```text
1. V2 相比 V1 没有变差，适合作为继续保留的补充分支。
2. 小规模收益更明显，主要来自 risk_refine_guard_T5S3。
3. 大规模收益很小，主要来自停滞后的 beam/nearest 增强。
4. 继续扩大 portfolio 的边际收益已经变低，后续应考虑减少弱分支、
   或把有效分支融合成一个自适应策略，而不是继续堆更多独立变体。
```

## 35. V3/V4：中转后的路线再均衡

V2 之后继续尝试的重点不再是增加中转次数 H，而是处理中转后 E2 路线之间的任务分布问题。

### V3：跨车重排

定义：

```text
同一卫星内，选择一条 E2 路线上的完整顾客任务对
delivery + pickup，将它搬移到另一条 E2 路线；
同步已有中转记录并做全局可行性检查；
只有真实总成本下降才保留。
```

这不是改变顾客 pickup 的中转归属，而是改变中转试探之后的二级路线任务归属。虚拟中转搬运节点不直接移动，统一由 `synchronize_transshipments` 根据任务所在路线重新校准。

84 个非北京算例实验结果：

| 对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Current M5 vs Model 2 | 48 | 34 | 0 | 2 | -2.52% |
| Optimized V2 vs Model 2 | 53 | 29 | 0 | 2 | -2.80% |
| Optimized V3 vs Model 2 | 56 | 26 | 0 | 2 | -3.31% |
| V3 vs V2 | 19 | 63 | 0 | 2 | -0.53% |

判断：V3 是当前新增优化里最有效的方向。它证明“中转后路线再均衡”确实值得做。

### V4：任务对交换

定义：

```text
同一卫星内，选择两条 E2 路线；
各取一个完整顾客任务对 delivery + pickup；
尝试互换两组任务，并重新寻找插入位置；
只有全局可行且总成本下降才保留。
```

84 个非北京算例实验结果：

| 对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Optimized V3 vs Model 2 | 56 | 26 | 0 | 2 | -3.32% |
| Optimized V4 vs Model 2 | 56 | 26 | 0 | 2 | -3.33% |
| V4 vs V3 | 3 | 79 | 0 | 2 | -0.01% |

V4 新增最终改善：

| 算例 | V3 | V4 | 胜出分支 |
|---|---:|---:|---|
| 100_customers_C209 | 2103.42 | 2097.85 | `swap_rebalance_guard_T1S1` |
| 50_customers_RC108 | 1549.65 | 1544.50 | `swap_rebalance_guard_T1S1` |
| 50_customers_C201 | 1459.92 | 1457.63 | `swap_rebalance_guard_T1S1` |

判断：

```text
1. V4 能改善不少单独分支，但最终新增赢点少。
2. V4 不适合替代 V3。
3. 若运行时间充足，可把 V4 作为大规模 portfolio 的可选补充分支。
4. 若运行时间有限，优先保留 V3。
5. 下一步更值得做的是把 V3 的重排思想嵌入搜索过程中，或者做自适应分支选择，
   而不是继续简单增加后处理分支数量。
```

## 36. V5：迭代跨车重排/交换

根据 V3/V4 的结论，继续尝试把“中转后的路线再均衡”从最终后处理提前到 ALNS 迭代过程中。

V5 的含义：

```text
每轮 ALNS candidate 完成受控中转试探后，
按固定间隔或搜索停滞条件触发一次小范围跨车重排/交换；
该重排仍属于 candidate；
若 candidate 被 ALNS 接受，则在该基础上进入下一轮；
若 candidate 被拒绝，则本轮中转和重排一起丢弃。
```

本轮测试 3 个分支：

| 分支 | 含义 |
|---|---|
| `v5_fixed_rebalance_T1S1` | 每 10 轮固定触发一次轻量跨车重排 |
| `v5_stag_rebalance_T1S1` | 搜索停滞 12 轮后触发跨车重排 |
| `v5_stag_swap_T1S1` | 搜索停滞后同时尝试跨车重排和任务对交换 |

84 个非北京算例实验结果：

| 对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Current M5 vs Model 2 | 48 | 34 | 0 | 2 | -2.46% |
| Optimized V3 vs Model 2 | 56 | 26 | 0 | 2 | -3.26% |
| Optimized V4 vs Model 2 | 56 | 26 | 0 | 2 | -3.33% |
| Optimized V5 vs Model 2 | 56 | 26 | 0 | 2 | -3.46% |
| V5 vs V4 | 8 | 74 | 0 | 2 | -0.14% |
| V5 vs Current M5 | 36 | 46 | 0 | 2 | -1.02% |

分规模观察：

| 规模 | V5 vs Model 2 | V5 vs V4 |
|---|---|---|
| 5_customers | 6 更好 / 24 持平 / 0 更差，平均 -0.99% | 1 更好，平均 -0.07% |
| 10_customers | 28 更好 / 2 持平 / 0 更差，平均 -5.03% | 3 更好，平均 -0.19% |
| 50_customers | 11 更好 / 0 更差 / 1 均失败，平均 -5.14% | 0 更好，平均 0.00% |
| 100_customers | 11 更好 / 0 更差 / 1 均失败，平均 -4.24% | 4 更好，平均 -0.36% |

V5 相比 V4 的 8 个新增最终改善：

| 算例 | V4 | V5 | 胜出分支 |
|---|---:|---:|---|
| 5_customers_R203 | 447.85 | 438.07 | `v5_stag_rebalance_T1S1` |
| 10_customers_C109 | 602.57 | 595.67 | `v5_fixed_rebalance_T1S1` |
| 10_customers_C201 | 582.34 | 573.96 | `v5_fixed_rebalance_T1S1` |
| 10_customers_C203 | 577.09 | 559.22 | `v5_fixed_rebalance_T1S1` |
| 100_customers_R201 | 2433.76 | 2417.15 | `v5_fixed_rebalance_T1S1` |
| 100_customers_RC101 | 3323.84 | 3296.65 | `v5_stag_swap_T1S1` |
| 100_customers_C109 | 1987.94 | 1952.96 | `v5_fixed_rebalance_T1S1` |
| 100_customers_C209 | 2097.85 | 2082.86 | `v5_stag_swap_T1S1` |

当前判断：

```text
1. V5 没有 V3 的跃升幅度大，但能在 V4 基础上继续无回退改善。
2. 固定间隔重排 v5_fixed_rebalance_T1S1 是最值得优先保留的 V5 分支。
3. 停滞交换 v5_stag_swap_T1S1 对 100 客户 C/RC 有补充价值。
4. 单纯停滞重排 v5_stag_rebalance_T1S1 赢点少，可以作为备选。
5. 后续优化重点应转向自适应选择 V3/V4/V5 分支，减少弱分支运行成本。
```

## 37. 最新口径：统一保留 Full V6

后续曾验证 Adaptive V6：5/10 客户跳过 4 个 V6 extra 分支、50/100 客户保留 Full V6。
84 个样本上 Adaptive 与 Full V6 质量基本持平，但为了避免实验口径分裂，当前最终决定是：

```text
只保留 Full V6 作为 Model 5 当前推荐版本；
所有规模都运行 V5 迭代重排/交换 + 4 个 V6 extra 分支；
不再区分 Full V6 / Adaptive V6 两套默认方案。
```

当前推荐命名：

```text
Model 5-Hybrid-Safety-Optimized-V6
```

Full V6 的 4 个 extra 分支：

```text
v6_fixed_rebalance_T2S1
v6_stag_swap_T2S1
v6_cand_refine_rebalance_T1S1
v6_risk_cand_rebalance_T5S3
```

84 个代表样本上的最终参考结果：

| 对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |
|---|---:|---:|---:|---:|---:|
| Stable V6 vs Model 2 | 56 | 26 | 0 | 2 | -3.49% |
| Stable V6 vs Current M5 | 37 | 45 | 0 | 2 | -1.04% |
| Stable V6 vs Optimized V5 | 8 | 74 | 0 | 2 | -0.06% |

当前后续优化重点：

```text
1. 不再继续拆 Full / Adaptive 两套 V6 口径；
2. 优先改进中转候选质量和跨车重排效率；
3. 小规模多跑 V6 extra 的计算浪费可以接受，优先保证报告和实验可比性。
```
