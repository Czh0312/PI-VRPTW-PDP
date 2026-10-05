# 配置

`cases/single_batch_demo.json` 是当前默认 B=1 示例；`cases/multibatch_demo.json` 是多批示例。
`baselines/` 是需显式 `--baseline` 的历史 M1–M4 案例。
`routing/` 的基础车队配置同时是派生数据来源，保留哈希与来源。
`cost/cases/multibatch_synthetic.json` 仅用于新版费用算术演示。其余历史费用参数保留为参数参考，不代表当前经济性验证。

LaDe V2 另有可显式选择的人民币参考配置：`cost/cases/lade_hangzhou_v2_single_batch.json`（M1–M4）和 `cost/cases/lade_hangzhou_v2_multibatch.json`（M5）。它们结合公开价格/工资代理与明确的情景假设，来源、公式和使用命令见 [参数核实说明](../reference/cost/05_lade_v2_economic_parameters_20260930.md)。它们不改变求解默认值，也不适用于没有公里尺度的 Zhou 坐标。M5 的 `container_lifetime_uses` 按实际发出箱次分摊，不等于 `pool_amortization_cycles` 的运营周期数；两种口径不能同时启用。

## 容器数量的统一入口

只修改 `routing/container_pools.json`，单次求解、批量实验和单批次模型比较默认共用这一份设置：

```json
{
  "demand_ratio": 1.0
}
```

总池量 `N = ceil(当前案例的总容器需求 Q × demand_ratio)`。总需求来自订单的 `container_count` 之和，与数据规模标签、顾客数无关。例如比例为 1.0 时，需求为 7、12、50 的案例分别使用 7、12、50 个容器，不会给所有小规模案例统一分配 131 个。
比例调成 0.6 时，以上案例分别使用 5、8、30 个。这里的 N 是周期开始时的清洁容器总数；各批次共用，回收、卸载、清洗完成后才能复用。B=1 要求 N ≥ Q，因此降低比例可能使单批次不可行；多批次是否可行取决于回收和清洗时刻。求解器不会自动调大 N。

无需额外指定配置路径，正常运行即可：

```bash
.venv/bin/python main.py route --case config/cases/single_batch_demo.json

.venv/bin/python experiments/runs/run_multibatch_cycle.py \
  --dataset-manifest data/manifest.json \
  --families zhou_Set1_7-2-1 --solver alns --seeds 42 --dry-run
```

`--dry-run` 显示每个任务实际采用的 `pool`。输出保存有效 N 和统一配置快照；实验输入冻结后，再修改统一配置不会改变该实验。数据集 policy 中原有的数量保留用于原始数据验收，日常调参无需修改。示例案例不再重复写数量，旧的逐数据集表和 3 × 3 表均已取消。

临时实验仍可用 `--container-pool-size 3` 指定单次运行的 N，或在批跑中用 `--pool-sizes 2 3 4` 扫描指定数量、`--suggested-pools` 扫描数据集预设数量；这些显式实验值优先于默认比例。`--pool-config 路径` 可替换统一配置文件，两种运行入口都支持。已冻结生成的案例中的显式 `container_pool_size` 用于复现，不会重新套用当前比例。
M1/M2 使用一次性包装，循环池约束只适用于 M3/M4/M5。比较批次对距离的影响时保持同一案例的 N 一致；最小池量研究另行扫描 N。
