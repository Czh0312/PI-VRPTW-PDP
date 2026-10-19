# 开发与维护

所有命令从仓库根目录执行。模型定义和公开入口见[模型布局](model_layout.md)，批量运行见[实验说明](../experiments/README.md)。

## 环境与命令

已有 `.venv/` 时直接复用。新环境可执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python main.py route --help
.venv/bin/python main.py compare --help
.venv/bin/python main.py compare-single-batch --help
.venv/bin/python main.py cost --help
```

`pyproject.toml` 集中保存运行依赖、开发依赖和测试配置；`.[dev]` 额外安装 pytest，
仅运行项目时可使用 `pip install -e .`。本项目按可编辑工作区使用，代码从当前仓库加载，
使数据与参数路径保持正确。已有环境的依赖齐全时可以继续使用，无需因文件合并重新安装。
依赖尚未锁定版本；复现实验还应核对原运行保存的 Python、依赖、输入及源码信息。
精确求解需要 Gurobi 许可证，ALNS 入口按需加载，不导入 Gurobi。

根目录保留项目入口 `main.py`、说明 `README.md`、统一工程配置 `pyproject.toml` 和 `Makefile`，
供安装、运行和测试使用。`.gitignore`、`.gitattributes`、`.gitmodules` 是仓库级 Git 配置；
`.ignore` 只控制源码搜索范围。它们按项目根目录生效，不混入业务参数 `config/`。
`.DS_Store` 和根目录临时 `gurobi.log` 可清理并已忽略；正式求解日志随对应实验保存在 `output/`。

## 从哪里修改

| 任务 | 入口 |
| --- | --- |
| 增加命令参数或调整单次运行输出 | `cli/main.py`、`cli/route.py`、`cli/multibatch.py`、`cli/multibatch_cost.py` |
| 修改模型编号、分流或输入范围 | `models/routing/catalog.py`、`service.py`、两种算法的 `model_n/` |
| 修改 M1–M5 求解实现 | 对应 `models/routing/model_n/exact/` 或 `alns/` |
| 修改 M6–M8 日历搜索及成本内核 | 精确：统一 MILP 与各模型 formulation；ALNS：各模型自己的 `k_search/scheduler.py` |
| 修改业务约束或独立审计 | `models/common/cycle/` |
| 调整场景参数 / 配置加载逻辑 | `config/` / `tools/configuration/` |
| 增加批量实验 / 分析 | `experiments/runs/` / `experiments/analysis/` |
| 构造数据、审计或恢复已有材料 | `tools/data/`、`tools/audit/` |

编号模型保留独立公开入口，求解服务经这些入口调用。基础业务层不导入求解器；普通精确与 ALNS 入口互不导入，求解算法不跨模型导入。旧 M5 混合入口已退役。各模型独立实现的位置详见[逐文件说明](../models/routing/README.md)。

## 源码检索

仓库保存大量逐次实验源码快照。根目录 `.ignore` 仅调整 ripgrep 等工具的默认检索范围，跳过 `output/`、`archive/` 和第三方 Java 仓库；它不改变 Git 跟踪、运行路径或冻结材料。

```sh
# 当前代码
rg --files models cli experiments tools tests
rg 'solve_exact' models/routing

# 显式检查历史结果，包括被默认搜索规则排除的内容
rg --no-ignore '批次' output/model35678_comparison/20261007_pool25_truck0p5_allK_5min_v1/report.md

# 核对本次工作区修改
git status --short
git diff --stat
git diff --check
```

全仓 `--no-ignore` 搜索会连同本地依赖与快照一起扫描；查历史时指定对应实验目录。

## 验证

```sh
# 日常基础检查：清单见 Makefile
make test

# 按本次改动选择专项测试；多个文件放在同一对引号内
make test TESTS="tests/routing/test_model5_cycle_cache.py tests/routing/test_model5_intensification.py"

# 需要完整回归时显式运行
make test-full
```

`make test` 覆盖模型入口、算法依赖隔离、周期审计、批次数域、费用和数据读取，
用于快速检查，仍需补跑具体改动涉及的测试。普通小改动不默认执行完整回归；
数学约束和审计修改还需检查对应模型、有效下界及周期重放，跨模型改动再扩大范围。

`pyproject.toml` 将测试发现范围限定为 `tests/`；原生 `.venv/bin/python -m pytest -q` 仍是完整回归。
所有测试继续可用，基础检查通过不表示完整回归已通过。许可证或可选依赖不足时，明确报告跳过/失败，不能把它们计为通过。
`make test PYTEST_ARGS="--durations=10"` 可定位当前检查中的耗时项。

## 实验保存与复现

新实验使用新的输出目录，记录配置、预算、随机种子、运行环境、原始结果及审计。日历段数 K、实际配送批次数、独立箱池 N 和需求箱次 Q 分别报告。限时可行、未找到解和已证不可行分别记录。

已有实验的输入、源码、结果和哈希保持原路径；整理当前源码时不批量改写这些快照。`output/` 中带 `run_study.py` 的脚本可能只服务相应的冻结实验，复现先读该实验报告。通用的新入口放入 `experiments/` 或 `tools/`。

旧定制精确算法及专用实验驱动已从当前树移出，见[研究归档](../archive/exact_research_20261019/README.md)。
复现早期实验应使用对应冻结源码，不将当前直接 Gurobi 替换进去。

已共享或分片保存的材料使用现有工具校验和恢复：

```sh
# M1–M4 / M5 benchmark：默认仅校验，共享源码一致后才可加 --restore
.venv/bin/python -m tools.data.restore_benchmark_sources --study output/experiments/m1_m4_comparison_20261004

# M6：默认校验共享源码索引；可用 --study / --run 缩小范围
.venv/bin/python -m tools.data.restore_model6_sources

# 大文件：仅验证分片重建后的字节，不覆盖原文件
.venv/bin/python -m tools.data.restore_large_artifacts --verify
```

`config/` 已按车辆、容器、运行规则、成本、求解器、数据生成和实验组合分类，
参数入口与覆盖顺序见[配置说明](../config/README.md)。`tools/configuration/` 目前保存读取和冻结配置的代码。
成果统一写入 `output/` 对应实验，公用路线绘图位于 `tools/reporting/route_plot.py`。
聊天总结由现有 skill 保存到 `project/`，参考资料继续使用 `reference/`。

`docs/` 只保留正在执行的计划及必要的当前说明。同一任务更新原计划，避免每轮新增一份记录。
阶段结束时将结论并入当前说明，正式报告随实验放到 `output/`，仍需追溯的开发记录移入 `archive/`；
已被替代且没有独立信息的草稿及时删除。每次修改相关模块时一并检查对应计划是否过期。
历史聊天交接见 [project/](../project/README.md)。
