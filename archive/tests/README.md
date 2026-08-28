# 历史测试归档

`legacy_data/` 中的测试仍引用已经从当前工作区移除的旧 Solomon 派生目录
（如 `data/5_customers/C101.csv`）。这些文件只用于追溯，默认 pytest 不收集。

如需恢复，必须先明确旧实例与当前 Zhou 数据的语义映射，不能直接替换文件名。
