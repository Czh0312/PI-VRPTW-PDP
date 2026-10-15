# 模型 4 数据集

小、中、大各9例，共27例。每例的 `case.json` 可直接运行，`parameters.json` 列出固定参数、优化决策及不适用的机制。

模型设置：`{"packaging": "reusable", "assignment": "optimized", "batch_count": 1, "pool": "Q", "objective": "distance"}`

最终运行范围以各例 case.json 为准；M6–M8 从完整正整数域中仅排除已证明不可行的批次数，共同物理输入位于本数据集 shared 目录。
