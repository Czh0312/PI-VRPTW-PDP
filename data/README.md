# 数据集

`basetest` 是当前实验使用的模拟基准数据集，包含小、中、大共 27 个算例。后续真实数据集放在与 `basetest/` 并列的目录下。[算例与配置](basetest/README.md)

每个数据集在自己的根目录放一份 `economic.json`，供该数据集的所有算例和模型使用。算例 CSV 和车队 `config.json` 放在同一个规模子目录。算例字段、作业约束和计费规则符合现有模型时，可以把 CSV 路径直接传给求解入口。[费率字段示例](basetest/economic.json)

```sh
python main.py route --case data/real_dataset/small/R01.csv --model 3 --solver alns --time 2
```

`--case S03` 这样的编号简写默认选用 `basetest`。标为 `calibrated` 的费率通过 `evidence_file` 提供校准依据。

原根目录的演示和实验预设已归档。加载旧配置时，程序仍接受原 `config/...` 路径。[历史配置](../archive/config/20261118_root_config/README.md)
