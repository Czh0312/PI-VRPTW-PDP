# 配置分类前的原始输入（2026-10-07）

保存分类前的 102 份 JSON 原文，供已有数据来源哈希与历史配置复现使用。
路径布局相对于原 `config/`，原始文件字节保持不变。

[迁移位置与原文件哈希](migration.json) · [当前数据集参数](../../../data/README.md)

`tools.configuration.loader.resolve_project_path` 支持这里的旧 `config/` 路径。归档值用于对应的历史运行。
不修改已有 `output/`、`outputs/` 或数据集的原始内容。历史脚本直接使用 `Path` 读取旧配置时，
应改用这里的对应归档路径；该回退只适用于当前配置加载器。
