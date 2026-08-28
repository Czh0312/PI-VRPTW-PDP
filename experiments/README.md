# 实验目录

当前实验代码按职责分为：

- `runs/`：运行精确、ALNS、模型扩展和交接验证实验；
- `analysis/`：读取已有结果并生成汇总表；
- `probes/`：预留给短期诊断探针，当前没有正式探针。

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
```

历史 C101/R101、北京案例、Model 7、列生成/分支定价探针和旧结果处理脚本位于 `archive/experiments/`。当前脚本应把结果统一写到 `output/`。
