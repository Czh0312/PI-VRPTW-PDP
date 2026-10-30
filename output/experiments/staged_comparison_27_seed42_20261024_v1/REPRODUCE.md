# 冻结证据复核与重新运行

正式环境为Python 3.11.3、Gurobi 11.0.3，原始求解器日志保留版本和单线程设置。使用项目依赖中的NumPy、Pandas、SciPy、Matplotlib等，以及可用的Gurobi接口与许可证运行精确求解。无需使用当前工作区的模型实现；本目录中的工具优先导入`sources/current/`。

## 仅复核已保存的证据

在配置好依赖的Python环境中，从仓库根目录执行：

```sh
python3 output/experiments/staged_comparison_27_seed42_20261024_v1/release_tools.py verify
```

这个命令不重新求解。它验证manifest、源码ZIP及所有登记输入、117次完成标记和原始结果哈希；重新独立重放109个可行解的两个连续周期，核对物理目标与费用算术，检查所有派生JSON/CSV表与发布清单。限时无可行解的8次仍保持原状态，不尝试将其改成可行或已证不可行。命令默认只输出检查结论；如需另存复核记录，用`verify --record <新文件路径>`，保留原发布记录。

原始case.json中记录了实验时的绝对路径，这些字节参与原始哈希，必须保留。`release_tools.py`只在临时case副本中将`inputs/`引用映射到当前下载目录，所有参数值及原始证据保持不变；Windows路径分隔符也会按记录解析。

## 显式重新求解

先确认原始证据复核通过，再选择单个job ID（完整清单在manifest.jobs中），将新结果写到本实验目录之外的全新目录：

```sh
python3 output/experiments/staged_comparison_27_seed42_20261024_v1/release_tools.py rerun \
  --job A_S02_m8_cost_exact_300s_s42 --output output/reproductions/staged_S02_new
```

工具调用冻结源码的相同公开入口，沿用登记的预算、种子和精确单线程设置；无外部初始解。新result、两周期audit及实际用时写入新目录，不改变本轮原始结果。限时算法在不同机器、负载或版本中未必返回相同方案；本轮结果由原始文件及其哈希定义。

如确需重新运行全部117次，使用`rerun --all --output <全新目录>`，按manifest顺序串行执行，预算上限合计33小时45分，另有提取和离线审计开销。此命令不会补入或替换正式分母中的任何调用。

原始`driver.py`及冻结的`experiments/runs/run_staged_model_comparison.py`保留完整实验控制与报告生成逻辑。它们中的prepare/run命令是历史实验驱动入口；复核和迁移后的复现优先使用上面的工具，以免将原路径当作当前路径。
