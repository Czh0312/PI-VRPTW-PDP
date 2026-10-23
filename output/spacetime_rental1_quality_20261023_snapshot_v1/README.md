# 27例八模型：阶段结果快照

这是按用户“先把结果推送到远程”的要求保存的阶段交付，**完整实验尚未完成**。
快照时间：2026-10-23T04:44:14.658357+00:00。原始冻结注册 SHA256：`0c69fe2d78ce8a9667c404d1a30a4f8b2ce258c5eadef8f36d115bddf58da9ea`。

已有精确最优证明 49 个模型算例；ALNS 已获得独立审计可行解
212/216 个模型算例。注册任务终态 1145/2880
（含条件跳过），完整预算、种子和补跑仍待完成，不能据此宣称最终求解完成。
M5–M8 容器租金按完整780分钟周期收取 `1×N`，购置成本为0；其他费用系数保持不变。

[原始阶段报告](study/report.md) · [逐次运行](study/tables/runs.csv) ·
[ALNS GAP](study/tables/alns_gaps.csv) · [时间效率](study/tables/efficiency.csv) ·
[覆盖率](study/tables/coverage.csv) · [快照状态](snapshot.json) · [完整性核验](verification.json)

精确已证最优才计算最优GAP；其余仅报告可行参照差异与下界证书。失败和待运行项不计0GAP。
M1–M4 的距离目标与 M5–M8 的费用目标分别比较。各预算为独立冷启动，不能把重跑视为连续搜索。
求解墙钟时间包含并发竞争；内存派发等待单独保存在 `dispatch.json`，不算入单次求解预算。
原表保留已有逐次耗时；所有失败及重跑的累计成本还需在最终核对中汇总。

捕获时未发现实验控制器或工作进程；原 `progress.json` 中 RUNNING 为失联前状态。
归档保留 2 个没有 completion 标记的尝试及 11 个资源保护终态，
详见 `snapshot.json`。这些记录没有被覆盖，后续恢复/补跑将单独登记，自动跟进继续保留。
本次未重新执行所有解的回放；核验的是冻结哈希、原始完成记录及其中的审计证据。

`study_raw.zip` 包含冻结源码、全部输入、算例配置，以及当前原始解、双周期审计、轨迹、
求解日志和故障记录。ZIP内文件名相对原实验目录；排除了缓存、锁、本机运行脚本及许可证。
ZIP和每个文件的SHA256分别记录在 `archive.sha256.json` 和 `files.sha256.json`。
可读的 `study/` 与ZIP中的对应文件逐字节一致，后续实验不会改动这份快照。

## 核验与恢复

在仓库根目录用本项目的Python执行：

```text
python -m experiments.runs.export_spacetime_quality_snapshot verify --output output/spacetime_rental1_quality_20261023_snapshot_v1
```

Windows本机按本地AGENTS.md通过 `tools/run.ps1` 的 `python` 模式执行上面的模块参数。
将ZIP解压到一个全新的目录即可恢复原始证据，勿覆盖活动实验目录。冻结配置保留原绝对路径
以维持哈希，若在其他机器复现求解，应另建工作副本并显式重定位配置路径及解释器；
重定位副本不能冒充原始字节哈希。使用归档中的 `driver.py`、`controller_io_adapter.py` 与
`source/`，保持 `manifest.json` 注册的模型、输入、全K域、预算、种子和租金设置。
已有解和审计不需要重新求解即可读取；长时实验和最终完整交付仍在后续跟进范围内。
