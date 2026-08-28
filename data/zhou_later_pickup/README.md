# Zhou 正向两级配送 + 延迟包装回收数据集

本目录以 Zhou 等人的 2E-VRPTWSPD 公开实例为正向配送底稿，只把总仓、前置仓、
顾客、坐标、配送需求、配送时间窗和服务时间视为来源数据。原论文的 pickup demand
不参与本文包装回收量或回收时间窗生成，仅保存在 `source_pickup_ignored` 字段中供审计。

来源：<https://github.com/zll-hust/2E-VRPTWSPD>

- 固定来源提交：`084f01f9302ebe4c0a42998167e2238ddc9cd353`
- 生成脚本：`tools/data/generate_zhou_later_pickup_dataset.py`
- 原始快照：`reference/zhou_2E_VRPTWSPD_input/`
- 实例数：40（Set1 22 个，Set2 18 个）
- 正向顾客记录：2618
- 回收周期情景：3
- 可直接输入求解器的情景 CSV：120

## 规模划分

为区分精确求解验证和大规模启发式实验，数据按顾客数分为两组：

| scale_group | 来源 | 顾客数 | 基础实例 | 情景 CSV | 用途 |
|---|---|---|---:|---:|---|
| `small` | Set1 | 7、10、12 | 22 | 66 | 小规模精确求解、模型一致性验证 |
| `large` | Set2 | 100、200 | 18 | 54 | 大规模 ALNS、算法性能与管理价值实验 |

当前数据不存在 13–99 个顾客的中间规模。生成器把 `customer_count <= 12`
归入 `small`、把 `customer_count >= 100` 归入 `large`；遇到中间规模会显式报错，
避免静默归错组。

## 回收时间窗

每个顾客的回收任务与本次配送任务是两个独立服务动作，并满足先配送、后回收。
为保证配送即使在原时间窗最晚时刻开始，也能先完成再进入包装使用期，采用：

```text
pickup_ready = delivery_due + service_time + usage_lag
pickup_due   = pickup_ready + pickup_window_width
```

三个情景如下：

| scenario_id | usage_lag | pickup_window_width | 含义 |
|---|---:|---:|---|
| short_same_day | 120 | 180 | 同日短周期，约 2 小时后可回收 |
| standard_same_day | 360 | 240 | 同日标准周期，约 6 小时后可回收 |
| next_day | 1440 | 360 | 约 24 小时后进入次日回收窗 |

时间单位沿用原 benchmark，并在本文场景中统一解释为分钟。

## 包装与分配假设

- 每名顾客对应 1 个可循环包装容器。
- 回收重量为 2、回收体积为 1，与 `config/routing/validation/zhou_small.json` 一致。
- 为兼容固定分配的 Models 1/3/5/6/7，顾客按欧氏距离预分配到最近前置仓。
- Models 2/4 可以忽略该预分配并重新优化顾客—前置仓归属。

## 文件结构

- `forward_nodes_normalized.csv`：只含一次规范化后的正向节点及来源审计字段。
- `manifest.csv`：实例规模、车辆容量、情景参数、输入文件路径和源文件修复审计记录。
- `repaired_source/Set1/`：5 个结构异常公开文件的规范化副本；原始快照保持不变。
- `small/<scenario>/`：7/10/12 客户的小算例，可用于精确求解。
- `large/<scenario>/`：100/200 客户的大算例，可用于 ALNS 实验。
- `small/manifest.csv`、`large/manifest.csv`：对应规模组的独立清单。
- `<scenario>` 为 `short_same_day`、`standard_same_day` 或 `next_day`。
- `metadata.json`：机器可读的数据来源、生成规则与统计信息。

## Set1 源文件结构修复

Set1 中 5 个公开文件存在额外行，其中 2 个文件的设施记录还带有顾客记录的多余列。
修复严格依据原作者 `Instance.readInstanceFileType2()` 的读取语义：按文件名读取前 N 条
顾客记录、随后 S 条卫星记录和 1 条仓库记录；保留 Java 实际读取的坐标，删除不会被
读取的行，并把设施记录统一为 `x y 10`（卫星）和 `x y 0`（仓库）。因此不会擅自从
额外候选行中替换节点，但会修正原先因多余列造成的卫星服务时间不一致。

修复后求解文件的 `source_extra_rows_ignored` 和
`source_nonstandard_facility_rows` 均为 0。原始问题仍通过
`source_original_extra_rows`、`source_original_nonstandard_facility_rows`、
`source_repair_applied` 和 `repaired_source_file` 完整保留审计轨迹。17 个原始干净实例
标记为 `clean`，5 个规范化实例标记为 `repaired`；论文可分别报告两组，也可在明确
披露修复规则后合并统计。
