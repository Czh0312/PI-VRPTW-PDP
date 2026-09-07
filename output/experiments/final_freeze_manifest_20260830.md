# 最终统一重跑冻结清单（2026-08-30）

- 基础提交：`7f41b3eda4733a614e1832117ace65c760bc6531`
- 已跟踪工作树差异指纹：`dee7d5ef283a890faa32b9a1b377e345dcb73633`
- 冻结时间：`2026-08-30T22:02:28.5270754+08:00`
- 场景：`standard_same_day`
- 六模型：`1, 2, 3, 4, 6, 7`

## 精确求解协议

- 数据：`small` 全部 22 个实例
- 每模型上限：120 秒
- 顺序：M1 → M2 → M3 → M4 → M6 → M7
- 继承：M1→M2、M3→M4/M6、M4→M7
- M4 formulation：`arc_pruning`
- 12 客户 M4 热启动：ALNS seeds 41/42/43，各 5 秒，3 workers
- 输出：`output/results/final_freeze_20260830/exact`

## ALNS 协议

- 数据：`large` 全部 18 个实例
- 每项上限：10 秒
- seeds：11/23/42/67/89
- 顺序：M1 → M2 → M3 → M4 → M6 → M7
- 继承：M1→M2、M3→M4/M6、M4→M7
- 输出：`output/results/final_freeze_20260830/alns`

运行期间不再修改求解器、实验 runner、配置或测试逻辑；只有结果和汇总报告允许新增。
