# 参数配置

按参数用途查找和修改；本目录不放订单、节点或距离矩阵。Python 读取与合并逻辑目前在
[`tools/configuration/`](../tools/configuration/loader.py)。

| 类别 | 目录 | 内容与入口 |
| --- | --- | --- |
| 车辆 | [vehicles/](vehicles/) | 一级/二级车辆数量、载重、容积、速度；按 Zhou 小例、大例、LaDe、机制例区分 |
| 容器 | [containers/](containers/) | 容器重量、体积、初始池量；[pool_ratio.json](containers/pool_ratio.json) 是 M1–M5 的统一需求比例入口 |
| 运行规则 | [operations/](operations/) | 周期、批次、装卸、清洗、周转、闭合规则；单批对比共用一份运行规则 |
| 成本 | [cost/](cost/) | `synthetic/` 算术示例、`lade/` 人民币参考参数、`scenarios/` 成本情景、`transport_ratio_v1/` 运输/容器比例参数 |
| 求解参数 | [solvers/](solvers/) | `exact/` 的预算和求解后端；`alns/` 的预算、迭代数、种子和算法选项 |
| 数据生成 | [datasets/](datasets/) | 指定版本数据集的生成规则、规模、时间窗和资源假设；不是运行时对已有数据的覆盖 |
| 实验组合 | [experiments/](experiments/) | 选择数据、模型、求解参数、成本与业务场景；`examples/` 是当前示例，`baselines/` 是显式历史基线 |

每组数据自身保存的 `fleet.json`、`policy.json` 是该数据版本的输入快照，不能通过修改它们
为已完成实验重新定义参数。要做新的参数实验，应在这里声明新组合。

## 常用入口

```sh
# 单批 M3 默认示例
.venv/bin/python main.py route --case config/experiments/examples/single_batch_demo.json

# 多批 M5 示例
.venv/bin/python main.py route --case config/experiments/examples/multibatch_demo.json

# M8 日历示例
.venv/bin/python main.py route --case config/experiments/examples/model8_demo.json
```

[六组案例的 M3/M5/M6/M7/M8 成本对比](experiments/pool25_truck0p5_allK_20261007/manifest.json)
按“数据集名称 + model_编号”命名，使用
[300 秒 ALNS 参数](solvers/alns/comparison_300s.json) 与
[运输/容器成本情景](cost/scenarios/truck_tricycle_pool25_truck0p5_clean02_v1.json)。
对应六份单批订单已移至 [data/experiment_inputs/](../data/experiment_inputs/pool25_truck0p5_allK_20261007/)。
[运输成本比例清单](experiments/transport_ratio_v1.json) 引用其专属费用参数，不混入车辆或算法参数。

## 参数引用与优先级

JSON 的 `$include` 列表引用其他参数文件，路径相对仓库根目录，也支持绝对路径。
例如 [多批实验配置](experiments/examples/multibatch_demo.json) 引用精确与 ALNS 参数，
再选择车辆和运行规则；车辆文件引用容器物理属性。

```json
{
  "$include": [
    "config/solvers/alns/multibatch_demo.json",
    "config/solvers/exact/multibatch_demo.json"
  ],
  "model": 5,
  "solver": "alns"
}
```

以上是引用方式片段，完整案例还需要数据、订单、车辆和运行规则。
列表按顺序合并，后面的同名值优先；当前文件自身的值最后覆盖。嵌套对象逐项合并，数组整体替换。
随后沿用原来的优先级：案例覆盖 `algorithm_config`，命令行显式参数覆盖案例。
循环引用会报错。`default.json` 只有被明确引用才生效，不会自动覆盖已有案例。

M1–M5 默认按 `N = ceil(Q × demand_ratio)` 计算池量，Q 是订单箱次总需求。
循环池约束适用于 M3–M5；显式 `container_pool_size`、`--container-pool-size`、实验池量扫描
以及 `--pool-config` 的原有优先级保持不变。设置不足时报告不可行，不自动扩大池量。
M6–M8 继续使用 `joint_batch_options` 的池上下限和日历允许域，不继承需求比例。

新实验会冻结被引用的参数文件，并保存供运行使用的合并值；修改当前参数不会改变已冻结的实验。
成本公式位于 `models/cost/`，价格来源与适用范围位于 [reference/cost/](../reference/cost/)。

## 旧路径与复现

2026-10-07 分类前的 102 份配置及订单原文保存在
[迁移归档](../archive/config/20261007_before_classification/README.md)，附原文件哈希和新位置对照。
当前加载器遇到已经移走的旧 `config/` 路径时，会读取归档原文，兼容旧案例与数据来源校验。
**当前调参使用上面的新目录**；旧路径指向历史值，不是新参数入口。
成本参数文件保留原文和哈希，其中 `previous_version` 等来源字段继续指向历史版本。

历史报告、实验结果和数据集保持原内容。直接使用文件系统读取旧路径的历史脚本，需要按对照表
指定归档位置；当前维护的运行入口已经更新。静态 V2 数据生成器仍读取原始资源输入，保持已有
数据与来源哈希；重新生成时如实记录当前生成器的源码哈希，不覆盖旧数据清单。
