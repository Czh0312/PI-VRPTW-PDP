# 大型实验文件的无损存储

数据集、实验结果、模型矩阵、冻结源码、日志和图表均随仓库保存。
以下两份原始实验文件超过 GitLab 免费项目的单文件 100 MiB 限制，
因此在本目录采用 gzip 压缩或分片存储；原文件路径在 `.gitignore` 中单独忽略。
本机原文件保留，实验源码和既有审计清单中的原路径、字节及 SHA256 不作改写。

| 原始文件 | 保存方式 |
| --- | --- |
| `output/experiments/m5_route_timing_20261001/probe_2.json` | 无损 gzip 压缩 |
| `output/experiments/m5_timed_integrality_20261001/probe_profile_models/joint/joint_inventory.mps.gz` | 原始压缩文件按 64 MiB 分片 |

克隆仓库后，在项目根目录运行以下命令恢复原路径：

```sh
python3 tools/data/restore_large_artifacts.py
```

仅校验保存的分片及恢复后的 SHA256，不写原路径：

```sh
python3 tools/data/restore_large_artifacts.py --verify
```

`manifest.json` 记录原始路径、原文件大小与 SHA256、编码方式及各分片校验值。
恢复工具会拒绝覆盖与清单内容不一致的已有文件。
GitLab 限制说明：<https://docs.gitlab.com/user/free_push_limit/>。
