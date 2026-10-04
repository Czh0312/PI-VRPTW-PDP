# 数据目录与模型求解文件夹整理

本次将当前案例放入 `data/single_batch/`、`data/multi_batch/` 下的小中大目录，
共同资源放入 `data/shared/`，统一索引为 `data/manifest.json`。
M1–M4 使用同一份单批数据对照；M5 使用多批数据；五个模型均有独立 `exact/` 与 `alns/` 文件夹。

- `before_layout.tar.gz`：整理前的当前源码、配置、测试、文档和旧混合数据目录，逐文件校验通过。
- `manifest.json`：备份中 1084 个文件的 SHA-256。
- `path_mapping.json`：每个数据文件的旧路径和新路径。
- `data_verification.json`：输入内容不变的核对记录。
- `previous_dataset/`：旧的混合数据目录，供追溯；它的路径引用仍记录迁移前位置。
- `previous_multibatch_readme.md`：旧入口说明。

没有删减数据或修改数学约束。已有求解结果、源码快照和输入快照没有重写；暂停任务仍使用自己的冻结副本。
恢复时先将压缩包解到独立临时目录，再选取需要的内容，不要直接覆盖当前工作区。
