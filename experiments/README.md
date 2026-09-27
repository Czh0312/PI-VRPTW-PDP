# 实验目录

当前实验代码按职责分为：

- `runs/`：运行精确、ALNS、模型扩展和交接验证实验；
- `analysis/`：读取已有结果并生成汇总表；
- `probes/`：机制验证与短期诊断脚本。

正式数据只来自：

- `data/zhou_later_pickup/`：小规模 7/10/12 客户和大规模 100/200 客户；
- `data/real_case/`：LaDe-D 杭州代表日与 OSRM 道路矩阵。

当前主入口：

```powershell
# 小规模精确批处理
python experiments/runs/run_mainline_small_exact.py --help

# 中大规模 ALNS 对比
python experiments/runs/run_model12346_alns_scaling.py --help

# Model 5/6 精确对比与 Model 6 交接验证
python experiments/runs/run_model5_model6_exact_comparison.py --help
python experiments/runs/run_m6_transshipment_validation.py --help

# 当前 Model 8 v3：不依赖 M3 基础解的联合精确/ALNS 验证
python -m experiments.runs.run_model8_joint_validation

# 以下为已归档的历史两阶段实验，显式导入 two_stage_v2
# 历史 Model 8 v2 与 M3：全部 22 个标准当日回收小算例
python -m experiments.runs.run_model8_small_comparison
python -m experiments.runs.refine_model8_small_exact
python -m experiments.analysis.summarize_model8_small

# 历史 Model 8 v2 与 M3：六个原始大算例，三种子与禁止交接对照
python -m experiments.runs.run_model8_large_comparison
python -m experiments.analysis.summarize_model8_large
# 对初轮含交接的正收益案例加强 M3 基线，结果单独保存
python -m experiments.runs.refine_model8_large_baseline
python -m experiments.analysis.summarize_model8_large

# 历史 Model 8 v2 同点多车分取：相同较好 M3 基线、三组搜索对照
python -m experiments.runs.run_model8_split_comparison
python -m experiments.analysis.summarize_model8_split

# 历史 Model 8 v2 实验扩展：固定/放开配送 × 禁止/允许传递
python -m experiments.runs.run_model8_delivery_flexibility
python -m experiments.analysis.summarize_model8_delivery_flexibility

# 历史 Model 8 v2：同一配送骨架下的容量协作与禁止交接对照
python -m experiments.runs.run_model8_capacity_probe --stage2-time 20 --seeds 42 7 19
python -m experiments.analysis.plot_model8_capacity
python -m experiments.analysis.summarize_model8_capacity

# 历史 M8 v1：同一个已证明最优的 M3 基础解（导入 legacy）
python -m experiments.runs.run_model8_two_stage --base-time 30 --stage2-time 20

# 历史 M8 v1 大规模试跑（导入 legacy）
python -m experiments.runs.run_model8_large_probe
```

历史 C101/R101、北京案例、Model 7、列生成/分支定价探针和旧结果处理脚本位于 `archive/experiments/`。当前脚本应把结果统一写到 `output/`。

`scenarios/model8_capacity.py` 是明确标注的机制构造例，仅检验容量释放是否能产生净里程收益，
不是正式基准数据或随机抽样证据。容量协作实验还单独报告原始 Zhou 100/200 客户结果。
