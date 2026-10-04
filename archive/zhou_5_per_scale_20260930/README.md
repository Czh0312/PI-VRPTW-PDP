# Zhou 三规模、每规模五例整理归档

2026-09-30，按用户要求将当前 Zhou 主对比集固定为 7/50/100 顾客各 5 例。
单批 B1 共 15 例，供 M1–M4 共用；相同场景配对 B2/B3 各 15 例，供 M5 使用。
LaDe 21 个基础场景和人工机制 2 个基础场景保持原状。

## 保存内容

- `before_changes.tar.gz`：变更前的数据、生成器、运行器、测试及相关说明；写入后逐文件核对哈希。
- `before_changes.json`：891 个备份文件的哈希，以及重新生成前固定的选样名单和规则。
- `previous_dataset/`：变更前的完整活动数据目录、清单及说明，供追溯。
- `selection_changes.json`：保留 36 组、退出活动清单 33 组、新增 2 组的具体名单。
- `preservation_verification.json`：旧清单 878 个产物校验；当前保留的 461 个运行输入/补充来源文件逐字节相同，13 个 Zhou 来源说明仅新增选样信息。
- `paused_run_verification.json`：原暂停精确实验的 458 个冻结输入与 133 个源码快照哈希，以及暂停状态。

原始 Zhou/LaDe 来源仍留在 `data/zhou_later_pickup/`、`data/real_case/`；旧实验输出保持原状。
活动清单为 `data/manifest.json`，当前算例入口见 `data/zhou_comparison.md`。

`previous_dataset/` 保持原字节，其配置中的路径仍指向归档前位置，因此只作追溯材料，不能直接当成当前可运行清单。
需要重建旧实验时，应使用其冻结输入/源码；需要完整恢复数据时，在隔离的项目副本中按备份相对路径还原，并使用备份中的生成器。
本轮没有执行正式优化求解，也没有恢复暂停任务。
