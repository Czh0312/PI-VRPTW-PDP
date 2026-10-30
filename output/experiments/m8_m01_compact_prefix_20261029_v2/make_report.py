"""Render saved evidence without changing the native experiment data."""
from study import HERE, read, sha, save


if __name__ == '__main__':
    s = read(HERE/'summary_reviewed.json')
    native = read(HERE/'summary.json')
    first_dir = HERE.parent/'m8_m01_compact_prefix_20261029_v1'
    first = read(first_dir/'summary.json')
    stock = read(first_dir/'auxiliary_stock_bound.json')
    old = read(HERE.parent/'m8_medium_optimized_20261029_v2'/'summary_reviewed.json')
    frontier = read(HERE/'stock_frontier.json')
    parallel = read(HERE/'parallel_summary.json')
    heuristic = read(HERE/'heuristic_summary.json')
    pairs = '\n'.join(f"| {r['K']} | {r['N']} | {r['lower_bound']:.6f} |" for r in frontier['retained_pairs'])
    validations = '\n'.join(f"| {name} | {v['tests']-v['failures']-v['errors']-v['skipped']} | {v['failures']+v['errors']} | {v['skipped']} |"
        for name, v in s['validation_xml_counts'].items())
    upper = s['audited_upper_bound']
    improvement = s['initial_audited_cost']-upper
    text = f'''# M01：20顾客中规模精确求解优化实测

最终已审计可行成本 **{upper:.9f}**，有效全局下界 **{s['global_lower_bound']:.9f}**，差距 **{s['gap_over_upper']*100:.4f}%**（分母为可行成本）。**{'已证明全局最优' if s['proven_optimal'] else '尚未证明全局最优'}**。原生日志、原始结果与下界来源均保留，未把松弛解当作可行方案。

## 范围、计时与已有信息

- 只使用M01：20个顾客、S5/S7各10个订单；只使用实验种子42。
- 本次先后做两轮设计对照，每轮预处理、建模、原生求解和提取审计合计限1200秒。第二轮总耗时{s['total_wall_seconds']:.3f}秒；不是一次20分钟内完成两轮。
- 原问题批次数为1至20。保留原时间精度、原车数、原物理趟数上限、容量、时间窗、最短使用时间、回收清洗库存和周期约束。
- 使用此前已通过原问题审计的成本{s['initial_audited_cost']:.9f}方案作为MIP start和成本支配上界。此成本不是本轮冷启动新发现。
- 第二轮同时进行了同一M01、种子42的ALNS上界搜索，共{heuristic['seconds']:.3f}秒，及8线程完整模型搜索，共{parallel['total_wall_seconds']:.3f}秒；均在主实验20分钟墙钟窗口内结束。它们单独记录，不冒充单线程性能。
- 启动器预检、源文件冻结及事后验证不计入算法预算。Gurobi预检已通过，接口版本11.0.3。

## 改动和实测

旧路线库下界路线先枚举完整任务子集，再装配超大主问题。新路线直接复用完整物理弧模型，以可选批次前缀共享E2车辆日程；再把必要时间、清洗库存和各站TSP距离下界按(K,N)组合加入模型。

| 方法 | 变量 / 约束 | 预算及耗时 | 已审计可行成本 | 有效全局下界 |
|---|---:|---:|---:|---:|
| 此前完整路线库下界路线 | {old['master_variables']:,} / {old['master_constraints']:,} | 600秒 / {old['total_wall_seconds']:.3f}秒 | {old['audited_upper_bound']:.6f} | {old['global_lower_bound']:.6f} |
| 第一轮：完整可选前缀模型，含独立库存下界 | {first['variables']:,} / {first['constraints']:,} | 1200秒 / {first['total_wall_seconds']:.3f}秒 | {first['audited_upper_bound']:.6f} | {max(first['global_lower_bound'],stock['global_lower_bound']):.6f} |
| 第二轮：组合下界、车辆编号规范与求解参数调整 | {native['variables']:,} / {native['constraints']:,} | 1200秒 / {native['total_wall_seconds']:.3f}秒 | {native['audited_upper_bound']:.6f} | {native['global_lower_bound']:.6f} |
| 第二轮窗口内：8线程完整搜索 | 同第二轮 | {parallel['total_wall_seconds']:.3f}秒 | {parallel['audited_upper_bound']:.6f} | {parallel['global_lower_bound']:.6f} |

旧表第一行是含松弛的路线列主问题，新模型是完整物理模型；变量数的差异是改写表示带来的，不是同一矩阵简单删行删列。不能把建模提速等同于证明最优的同比提速。

第二轮运输证书耗时{s['certificate_seconds']:.4f}秒，库存组合预处理耗时{s['frontier_seconds']:.4f}秒，完整模型建模耗时{s['build_seconds']:.4f}秒。原20个K先由运输/成本证书保留2、3、4、5，再由库存证书排除5，最终保留以下8组：

| 批次数K | 箱池N | 库存松弛成本下界 |
|---:|---:|---:|
{pairs}

只有原生不可行证明或下界高于已审计上界才排除组合；未知或超时组合保留。完整模型没有采用启发式趟数上限。最终方案为K={s['selected_K']}、N={s['selected_N']}，相较输入方案降低{improvement:.6f}，约{100*improvement/s['initial_audited_cost']:.4f}%。最终上界来自 `{s['final_upper_bound_source']}`。

## 上下界的出处

- 单线程完整模型原生状态：{native['status']}，原生下界{native['native_lower_bound']:.9f}，原生可行成本{native['native_objective']:.9f}，节点{native['nodes']:.0f}，原生耗时{native['native_runtime']:.3f}秒。
- 8线程完整模型原生状态码：{parallel['native_status']}，下界{parallel['global_lower_bound']:.9f}，节点{parallel['nodes']:.0f}，原生耗时{parallel['native_runtime']:.3f}秒。
- 解析证书和库存松弛给出的全域下界为{frontier['global_lower_bound']:.9f}；最终采用所有有效下界的最大值。`summary.json`保留单线程原始指标，`summary_reviewed.json`才是组合后的结论。
- ALNS只贡献经审计上界，不贡献全局下界或不可行证明。此前第一轮受限构造的失败也没有用作任何剪枝证据。
- 第二轮8线程辅助搜索首次启动时，Gurobi无法打开包含中文绝对路径的日志文件，尚未进入优化。改用仓库相对路径后重启；首次失败保存在`parallel_controller.log`，有效运行保存在`parallel_controller_retry.log`，没有重置主实验截止时刻。

## 为什么小规模收益大，中规模仍未闭合

小规模的十几秒结果使用了已审计可行上界和下界主问题，多数案例下界很快贴住上界，因此无需搜索大量完整方案。这个加速没有人为限制为7顾客；中规模也能受益于预处理和改写模型，但本例的乐观运输/库存下界仍允许比实际物理日程便宜的方案。

M01每站有20个配送/回收任务，完整路线库实际产生1,268,786列、枚举285,828,896个前缀。改写后避免了这一枚举步骤，但完整模型仍须同时决定订单批次、回收时点、车辆访问顺序、等待、清洗和箱池复用。日志中的大量分数决策和每个分支较重的线性求解说明，剩余时间花在联合可行性及最优性证明上。20个顾客本身不能直接换算为确定求解时间。

本次实证支持“可以大幅改善表示和下界”，不支持“20分钟已经证优”，也不能据此断言继续延长若干分钟一定证优。后续有针对性的方向是按(K,N)分解并加入路线时间/回收兼容性证据，或用列生成避免预先生成全部路线；本报告没有把尚未实现的方法列作已完成优化。

## 验证记录

| XML记录 | 通过 | 失败/错误 | 跳过 |
|---|---:|---:|---:|
{validations}

`validation_regression.xml`选择了物理下界、时间预处理、可选前缀、库存松弛、组合下界5个测试模块，共44项；首次43通过、1失败、0跳过。该失败是已有基线在测试时限内未证明最优，未显示最优值不一致；同一项未改测试和源文件单独重跑，1通过、0失败、0跳过（`validation_retry.xml`）。不能把首次运行描述为全部通过。

`validation_start.xml`另外验证实际M01已有方案在全部新约束和编号规范下可行。`validation_evidence.xml`复核冻结源输入、完整K域与72个物理E2趟位、全部20组库存证书、独立TSP模型、原问题成本和两周期回放、已有方案向库存松弛的投影，以及辅助搜索的证明范围。不是全仓库测试。

## 复核文件

`manifest.json`、`frozen_sources_inputs.zip`、`stock_frontier.json`、`transport_certificate_initial.json`、`solver_parameters.json`、`gurobi.log`、`parallel_gurobi.log`、`summary.json`、`summary_reviewed.json`、`final_best_audited_result.json`及`independent_final_audit.json`。所有冻结输入和源文件的SHA-256均重新核验；计时实验没有中途修改模型源文件。

研究实现：`experiments/runs/model8_compact_prefix.py`、`model8_stock_relaxation.py`、`model8_frontier.py`；原默认求解入口保持原选择，本次研究入口为本目录`study.py`。方法、适用范围和拒绝条件见`docs/model8_compact_prefix.md`。
'''
    (HERE/'report.md').write_text(text, encoding='utf-8')
    save('evidence_hashes.json', {str(path.relative_to(HERE)):sha(path)
        for path in sorted(HERE.iterdir()) if path.is_file() and path.suffix in ('.py','.json','.xml','.md')
        and path.name != 'evidence_hashes.json'})
    print(str(HERE/'report.md'), flush=True)
