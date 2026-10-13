# E1b 状态文件替换失败记录

发现时间：2026-10-14 05:17（Asia/Shanghai）。事件时间：04:57:14。

首次失败任务：`e1b_zhou_Set2_100-10-1_subset50__spread_medium_m8_alns_final_t300_s42/attempt_001`。
控制器在运行约 57.74 秒时替换 `live.json` 收到 WinError 5（拒绝访问），随后按冻结驱动的异常处理终止 worker。worker.log 为空，没有 result.json 或 audit.json；不能推断模型不可行或审计失败。

原始 row.json、completion.json、process.json、launch.json、live.json 和未成功替换的临时文件全部保留。首次状态仍为 ERROR，统计分母仍包含此任务。

事件发生在上轮监控 Get-Content 读取 live.json 附近；未捕获当时句柄，因此不能断言唯一锁定来源。后续停止读取运行中 live.json 的内容，只查看文件 LastWriteTimeUtc，并通过不再修改的 launch.json 获取 PID。必须读取的 progress/pipeline JSON 改用 tools/data/read_thesis_json_shared.ps1，显式共享 ReadWrite/Delete，立即关闭句柄后再解析，缩短持有时间。冻结驱动、源码和输入不修改。

验证限制：沙箱及正常用户环境中的临时文件试验，均在持有显式共享删除的读句柄时遇到 File.Move 替换拒绝；因此不能声称这个共享读取函数已消除冲突。它仅缩短必需读取的窗口，关键措施是停止轮询 live.json 内容。未运行 Python 或求解器测试。后续恢复控制器应对短暂状态文件替换失败有有界重试；这不能通过修改当前冻结文件实现。

04:57 的 heartbeat_checks.jsonl 快照已含 ERROR，但该条 work 叙述沿用了稍早 166/166 的读取结果，未及时反映刚发生的错误；本记录纠正该叙述，原记录不覆盖。

## 接续要求

当前控制器继续完成其余首次尝试，不并发启动补跑。冻结驱动会在 E1b 全部终态后生成 ready=false 门槛，formal pipeline 应停在 NEEDS_REVIEW，不得直接放行 E2。

届时核对控制器和 worker 均结束，保存首次失败门槛；在正常运行时预检成功后，以相同冻结源码、输入、预算、种子和资源补跑失败任务（新 attempt_002，追加预算 300 秒，另列恢复统计）。即使补跑通过，当前门槛实现只检查首次尝试，不会自动恢复 ready。需增加有原始失败哈希和恢复审计证据的显式基础设施事件复核，严格限制为该事件，不能覆盖首次失败或豁免任何求解、审计或证书矛盾。该复核尚未完成，不能宣称阶段通过。

E1b 最终论文表仍采用首次尝试，单列失败造成的不完整配对及补跑敏感性分析；恢复结果不能替换主统计。
