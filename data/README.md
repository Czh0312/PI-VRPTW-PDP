# 数据集入口：单批次与多批次

现行版本为 **`static_batch_suite_v2` / `operational_scenarios_v2`**。2026-09-30 已按空间、时间窗、使用时长及资源审查结果重建；详见 [修订与验收记录](../docs/dataset_redesign_v2_20260930.md)。

业务口径是“静态全信息、当天配送、顾客卸货或短时使用后交回周转箱”，为明确的研究情景，不是实测消费者包装回收行为。非机制例的周期为 **08:00–21:00**，包括末轮回仓、清洗和周期闭合。

## 目录与数量

```text
data/
├── single_batch/{small,medium,large}/<family>/   B1，M1–M4 共用
├── multi_batch/{small,medium,large}/<family>/    B2/B3，供 M5 使用
│   ├── b2/
│   └── b3/
├── shared/{small,medium,large}/<family>/         共同网络、车队、来源及道路矩阵
├── manifest.json                                案例清单、配对关系与输入哈希
├── zhou_comparison.md                           Zhou 70 例及运行入口
├── source_inputs/mechanism/                     人工机制来源底稿
├── zhou_later_pickup/                           Zhou 来源资料
└── real_case/                                   LaDe 派生底稿与道路快照
```

每个案例包含 `case.json`、`orders.json`、`policy.json`。同一 `family_id` 的 B1/B2/B3 共享网络、订单数量、送取窗、使用时间、车队、默认池和周期边界，仅配送批次映射与日程不同。M1/M2 是纯配送，M3/M4 包含容器回收；M2/M4 可以自由选仓。精确与 ALNS 仍在各模型的独立文件夹管理。

| 规模 | 顾客数 | Zhou B1 算例 | 配对 B2/B3 配置 |
| --- | --- | ---: | ---: |
| 小型 | 7、10、12，各 10 例 | 30 | 60 |
| 中型 | 20、30，各 10 例 | 20 | 40 |
| 大型 | 50、100，各 10 例 | 20 | 40 |
| 合计 | 七档 | 70 | 140 |

LaDe 另有 10/20/30 AOI×7 天，共 21 组；人工机制 2 组。全部来源共 **93 个基础场景、279 个配置**。全部单批小/中/大为 39/34/20 例，多批为 78/68/40 个配置。机制例不计入七档十例。

## 空间、需求与资源规则

- Zhou 每档仍为 5 个来源块×2 个时间情景；两情景使用相同顾客子集、仓库、固定分区、数量、车队和需求阶段，只改变使用/回收节奏。
- 20/30/50/100 顾客子集按同一母例固定排序嵌套。现行 70 情景有 35 套几何、20 个不同来源实例；不是 70 个独立空间样本。算法种子重复另计。
- 顾客和保留的前置仓坐标来自原资料。仓库只从来源候选点中选取；固定分区按距离和公开的区域顾客数上限生成，并为每个保留仓分配顾客。全部模型共用相同候选仓集合。
- 同坐标顾客保留为不同订单，不擅自挪动或删除。Zhou 的距离仍是基准坐标单位，不能直接当公里。
- Zhou 箱数按 `ceil(货量/15)` 构造，重量保留来源值；这是包装映射假设。LaDe 保留 AOI/日配送量与位置，并使用既有道路矩阵；一包裹一箱、重量、仓址代理及回收规则仍属假设。

| Zhou 顾客数 | 仓数 | 每仓二级车辆 | 二级车辆总数 |
| ---: | ---: | ---: | ---: |
| 7 | 2 | 1 | 2 |
| 10 | 2 | 2 | 4 |
| 12 | 2 | 2 | 4 |
| 20 | 3 | 2 | 6 |
| 30 | 3 | 3 | 9 |
| 50 | 4 | 4 | 16 |
| 100 | 5 | 5 | 25 |

Zhou 二级容量为 6 个体积单位；LaDe 二级容量为 300 kg、1.2 m³，每仓 1 或 2 辆。所有参数在 [统一设计配置](../config/datasets/static_batch_v2.json) 中预先指定，不按每例求解结果扩车。一级车仍按仓配置，当前不是专门的一级车紧缺基准。

## 时间与回收

| 批次配置 | 补货开始 | 回收截点 |
| --- | --- | --- |
| B1 | 08:00 | 19:45 |
| B2 | 08:00、16:00 | 15:00、19:45 |
| B3 | 08:00、12:00、16:00 | 11:00、15:00、19:45 |

最后一轮预留至 20:45 回仓、21:00 完成清洗闭合。单批次表示一次一级补货，二级车辆仍可执行多趟服务；顾客可在当天不同时段收货。订单在周期开始前全部已知，服务窗不是订单产生时刻。

使用时间、需求阶段和预约回收延迟使用不同哈希流，消除 LaDe 原来的同序号耦合。短使用情景取 20/30/40 分钟，较长情景取 30/45/60 分钟；不能把这些数值称为实测。窗口同时满足声明的配送截止与最短使用时长，不再隐含更早的配送期限。

窗口围绕公开的确定性构造时间表生成，用于保证兼容性；参考路线保存在 `provenance.json`，仅用于数据可行性审计，求解器不会自动读取。它们不是最优解。该构造会带来已知结构，未来实测数据用于外部验证。

## 箱池与实验口径

N=Q 保留为共同的宽松参照，也是当前单批规则下的必要池量。多批研究应使用清单中的 `suggested_pool_sizes`：最大批需求，以及总需求量的 50%/65%/80%/100%（取整去重）。低档可能不可行，必须报告，不能自动扩大箱池。

共享运行配置仍为 `config/routing/container_pools.json` 的需求比例 1.0；`--suggested-pools` 显式覆盖它。只跑默认 N=Q 不能代表完成了有限箱池实验。距离最优也不等于经济成本最优。

```sh
# 查看单批主对比集
.venv/bin/python tools/data/static_batch_dataset.py --groups single_batch \
  --source-kinds zhou_derived_scenario --paths-only

# 预览单批四模型精确任务，不启动求解
.venv/bin/python experiments/runs/run_single_batch_exact.py \
  --source-kinds zhou_derived_scenario --dry-run

# 预览小型多批箱池扫描，不启动求解
.venv/bin/python experiments/runs/run_multibatch_cycle.py \
  --dataset-manifest data/manifest.json --groups multi_batch --sizes small \
  --source-kinds zhou_derived_scenario --suggested-pools --solver alns --dry-run

# 构造可行性与两周期审计；输出目录须不存在或为空
.venv/bin/python tools/audit/audit_static_batch_suite.py \
  --manifest data/manifest.json --output /tmp/static_v2_audit

# 检查所有公共模型 ALNS 入口的初始化；不进行正式性能比较
.venv/bin/python tools/audit/validate_static_batch_v2.py --output /tmp/static_v2_entries
```

## 版本与追溯

V1 数据及改动前代码保存在 `archive/dataset_design_v2_20260930/`，1167 个原受管数据文件已归档并校验。旧实验、原始来源、暂停求解的冻结输入与源码均保留。历史结果属于原数据版本，不能直接与 V2 按同名案例混合统计。

生成器拒绝覆盖内容不同的现存文件；调整参数时先生成独立候选目录并验收。当前版本与试制过程、完整测试、有限预算求解检查见 [修订记录](../docs/dataset_redesign_v2_20260930.md)。
