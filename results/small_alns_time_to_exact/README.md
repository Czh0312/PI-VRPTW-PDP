# 小规模ALNS time-to-target实验

- 数据：22个`small/standard_same_day`算例。
- 模型：M1、M2、M3、M4、M6，共110次ALNS。
- 随机种子：42。
- 单次最长时间：20秒。
- 命中标准：首次达到已证明的精确最优值，绝对容差1e-5。

`small_time_to_exact_t20_seed42_20260826_191920.jsonl`/`.csv`为完整明细。实验中43/110次在20秒内命中；未命中记录不计入“节省时间”条件均值。

完整解读见`output/experiments/small_alns_time_to_exact_experiment.md`。
