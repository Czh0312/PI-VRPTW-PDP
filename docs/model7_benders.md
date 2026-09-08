# Model 7 逻辑型 Benders 分解

Model 7 已新增可选的 `algorithm="benders"` 精确入口，原有分仓枚举入口保持默认。
它针对自由分仓造成的组合爆炸，固定分仓后的路径与交接仍由完整 Model 6 求解。
Model 6 本身的固定分仓 MILP 未改动，Model 1–6 尚未新增 Benders 入口。

## 分解结构

主问题选择顾客分仓变量 `w[c,s]`，并用 `theta` 表示 E1 与 E2 总距离。
默认主问题使用 Model 4 的 **E2 零回收载荷松弛**：保留配送/回收访问、时间窗、
先送后取、E1–E2 衔接、原始配送载荷与 E1 回收载荷，仅将 E2 回收重量和体积置零。
主问题的服务路线仍含整数变量，它比完整自由分仓交接模型少了交接变量。
新开关只供构建松弛使用，普通 Model 4 的默认数学模型不变。

给定主问题选出的分仓，子问题调用完整 Model 6，联合优化 E1/E2 路径和整数空箱交接。
原有 Model 6 无交接证书、分仓松弛筛选仍可使用。子问题存在整数路径变量，因此这里
采用逻辑型 Benders，而不是直接从整数子问题读取 LP 对偶乘子。
方法背景见 [Hooker 的 LBBD 综述](https://arxiv.org/abs/1910.11944)。

定义与第 k 轮分仓的差异数：

\[
\Delta_k(w)=\sum_{c\in C}(1-w_{c,s_k(c)}).
\]

不可行子问题产生排除割 `Delta_k(w) >= 1`。若子问题返回成本 `q_k`，添加
条件最优性割：

\[
\theta\ge q_k(1-\Delta_k(w)).
\]

由于距离非负，这个割只在相同分仓处发挥作用。当子问题已找到可行完成方案时，
使用其当前最佳目标值加强该分仓的成本割；没有可行完成方案时使用求解器下界。
主问题全局下界与已保存路线的可行上界闭合后报告 `proven_optimal=True`。
单纯无解超时不会生成不可行排除割。

可通过 `master_relaxation="flow"` 切换为更小的连续流主问题，包含分仓容量约束、
时间窗预筛选、E1/E2 连通流与配送重量/体积流松弛。它适合做消融比较，但延后回收
带来的额外里程未充分进入下界，可能退化为逐个检查所有分仓。
所有主问题使用距离和行驶时间的有向最短路闭包，以支持非对称、非度量的输入矩阵。
服务时间及载荷必须非负。零货物需求的顾客行自动使用流主问题。

## 使用

```bash
python main.py route --case config/cases/zhou_small_demo.json \
  --model 7 --solver exact --exact-algorithm benders \
  --exact-time 300 --benders-subproblem-time 5
```

`--exact-time` 是整个精确流程的预算；`--time` 仍是 ALNS 的预算。
输出放在 `output/single_runs/model7_exact_benders/`，证书 JSON 包含每轮分仓、
子问题状态、上下界、割数量和主/子问题耗时。

Python 接口：

```python
from models.routing.variants.model_7 import solve_exact

e1, e2, assignment, info = solve_exact(
    problem, algorithm="benders", time_limit=300,
    return_info=True, return_incumbent_on_time_limit=True,
    benders_options={
        "master_relaxation": "service",
        "master_time_limit": 10.0,
        "subproblem_time_limit": 5.0,
        "absolute_tolerance": 1e-6,
    },
)
```

子问题重复遇到同一分仓时，时间片按 2 倍增加，总预算始终优先。
`max_iterations` 可用于消融或调试。也可提供完整 M4 无交接路线、分仓和目标值作为
热启动；它们会先经过独立路线审计。终止时的 incumbent 可返回，但必须检查证书字段。

## 初步对比

2026-09-08，本机 Gurobi 12.0.3、1 线程、Seed=42、相同车辆配置、每算法 30 秒预算。
Benders 子问题初始时间片为 5 秒。原方法包含原有全局松弛证书及分仓筛选。

| 七客户算例 | 原方法 | Benders（服务主问题） | Benders 实际分仓子问题数 |
| --- | --- | --- | --- |
| Set1_7-2-1b | 0.74 秒最优，304.151030 | 1.20 秒最优，304.151030 | 1 / 128 |
| Set1_7-2-2b | 30 秒未闭合，229.930459，Gap 4.28% | 8.28 秒最优，220.098705 | 1 / 128 |

这是两个算例的一次运行，不能据此推断所有规模均加速。第一个算例原有证书更快；
第二个算例 Benders 更快获得更优可行解并闭合证明。流主问题在第一个算例检查了全部
128 个分仓，耗时约 21 秒，因此默认采用服务主问题。

完整结果：`output/experiments/model7_benders_service_20260908/summary.json`。
复现实验：

```bash
python -m experiments.runs.run_model7_benders_comparison \
  --dataset data/zhou_later_pickup/small/standard_same_day/Set1_7-2-1b.csv \
  --dataset data/zhou_later_pickup/small/standard_same_day/Set1_7-2-2b.csv \
  --time 30 --threads 1 --subproblem-time 5
```

新增回归覆盖完整分仓对照、逐分仓主问题下界、独立路线审计、完整 M6 子问题回退、
不可行割、无解超时、零时间预算、热启动审计、非度量距离和服务层参数分发。
