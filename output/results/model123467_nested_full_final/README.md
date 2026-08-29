# M1/M2/M3/M4/M6/M7 incumbent 保留全样本实验

- 数据：Zhou `large/standard_same_day` 的 17 个公共可行实例；
- 配对：M2←M1、M4/M6←M3、M7←M4；
- 基础搜索：10 秒；扩展搜索：2 秒；
- 随机种子：42；
- 正式结果：`nested_large_base10_ext2_seed42_20260829_200502.*`；
- 共 68 行，全部可行，全部保持扩展模型不劣于继承 incumbent。

上一轮 `output/results/model123467_nested_full/` 在审计时发现 M4 会重建继承的
M3 一级回收路线，因而只保留为诊断记录，不进入正式统计。
