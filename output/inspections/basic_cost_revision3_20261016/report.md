# 基础成本修订3与异机重跑核验

## Material Passport

academic-research-suite / experiment-agent；validate；VERIFIED（输入、成本算术和小规模流程）；2026-10-16。六次短测用于技术验证，不是完整性能实验或总体统计结论。

## 当前费用

`C = 0.1*D1 + 0.05*D2 + 5*N + 0.01*Q + 0.01*Q`。清洗与追踪合并一次计费；磨损直接按次计费。工时、启用、趟次与单独追踪费用均为0。配置箱子购置费一次计入5*N；另列的initial_pool_investment展示同一笔金额，不再叠加。

公共参数源为config/experiments/benchmark27_v1/economic.json，27份费用快照全部一致。999份物理输入和决策域文件与修订2逐字节一致。M1–M5原生距离目标保留，M6–M8及循环模型统一费用评价使用新系数。原正式结果及先前算法诊断的冻结资料保留旧费用，不能当作修订3结果。

## 已执行验证

- 全部216份配置通过输入审计，1092个文件哈希有效。
- 新费用下重新生成216份显式可行调度并独立重放通过。
- 测试覆盖347项：成本模块44项通过，扩展回归302项通过、1项旧断言失败；复核原HEAD成本代码确认旧夹具应为61，修正后8项定向复测通过。
- 修订3冻结流程：S01的M6/M7/M8分别运行exact 3秒、ALNS 2秒，共6次首次尝试全部可行、独立审计通过；其中2次证明最优。六份方案的费用均另按新公式重算一致。smoke_v2/stage_gate.json为ready。
- 原预检因读取未声明的SciPy版本而在启动求解前失败；失败信息保留，驱动现改为登记项目实际声明的依赖。成功流程另存在smoke_v2，未覆盖失败输出。
- 修订2费用、来源、清单及见证原字节归档在revision2_cost_and_validation.zip，哈希见revision2_archive.json。

## 其他电脑重跑

拉取main后，按docs/benchmark27_remote_run.md重新prepare到全新目录（例如output/experiments/benchmark27_revision3_formal），再执行新driver.py。恢复旧冻结driver会继续使用旧源码和旧费用。完整实验由其他电脑执行，本机仅做上述短测。
