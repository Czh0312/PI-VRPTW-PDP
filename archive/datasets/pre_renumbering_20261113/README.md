# 模型重编号前的冻结数据

[旧八模型数据集](data/formal_benchmark27_uniform_7_20_50_v1/README.md)和[旧实验配置](config/experiments/)保留原文件内容。当前输入见[六模型数据集](../../../data/README.md)。

旧费率、车队与容器配置用于历史复现。原 `config/` 路径可由当前配置加载器解析到这里。[旧费率](config/cost/) · [旧车队](config/vehicles/) · [旧容器配置](config/containers/)

原 `data/formal_benchmark27_uniform_7_20_50_v1` 及同批旧配置的路径可由当前配置加载器解析到这里。用历史源码复现旧实验时，优先使用实验自带的冻结输入和源码。需要重建原目录结构时，将本目录的 `data/`、`config/` 布置到对应的历史工作副本。
