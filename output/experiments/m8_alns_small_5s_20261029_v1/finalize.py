"""Offline report and provenance; does not run or alter either solver."""
import shutil
from bench import HERE, ROOT, CASES, SECONDS, TOLERANCE, read, save, sha, source_hashes

plan, data = read(HERE/'plan.json'), read(HERE/'comparison.json')
assert source_hashes() == plan['source_hashes']
assert sha(HERE/'bench.py') == plan['driver_sha256']
publication = read(ROOT/'output/experiments/m8_alns_joint_repair_20261029_v1/publication.json')
assert all(plan['source_hashes'][path] == digest for path, digest in publication['algorithm_files'].items())
references, diagnostics = [], []
for row in data['rows']:
    case = row['case']
    reference_folder = ROOT/f'output/experiments/m8_all_small_rerun_20261029_v1/runs/{case}/s42'
    proof = read(reference_folder/'summary.json')
    witness = read(reference_folder/'best_audited_result.json')
    result = read(HERE/f'runs/{case}/result.json')
    assert result['economic_profile_hash'] == witness['economic_profile_hash']
    assert result['calendar_config'] == witness['calendar_config']
    references.append(dict(case=case, initial_U=proof['initial_U'], final_optimum=proof['audited_upper_bound'],
        initial_already_optimal=abs(proof['initial_U']-proof['audited_upper_bound']) <= TOLERANCE,
        historical_feasible_plan_used=proof['historical_feasible_plan_used'],
        cold_start=proof['cold_start_end_to_end'], wall_seconds=proof['total_wall_seconds'],
        optimal_K=proof['selected_K'], optimal_N=proof['selected_N'],
        summary_sha256=sha(reference_folder/'summary.json'), witness_sha256=sha(reference_folder/'best_audited_result.json')))
    selected = [visit for visit in result['count_search_visits'] if visit['batch_count'] == proof['selected_K']]
    diagnostics.append(dict(case=case, optimal_K=proof['selected_K'], ALNS_K=row['K'],
        optimal_K_allocated_seconds=sum(visit['budget'] for visit in selected),
        optimal_K_iterations=sum(visit['iterations'] for visit in selected), visits=selected))
save(HERE/'reference_timing_audit.json', references)
save(HERE/'K_budget_diagnosis.json', diagnostics)
assert all(row['initial_already_optimal'] and row['historical_feasible_plan_used'] and not row['cold_start'] for row in references)

lines = ['# 当前默认模型八 ALNS：九例各5秒，根种子42', '',
    f"**平均GAP {data['mean_gap_percent']:.4f}%；最差GAP {data['worst_gap_percent']:.4f}%；"
    f"5秒内达到已知最优值 {data['known_optima_recorded_within_5s']}/9。** "
    f"{data['valid']}/9个输出通过完整独立业务审计及两周期回放。", '',
    '## 口径', '',
    '使用当前已发布的条件式时间循环补救版本，九个七单算例S01—S09，实验根种子仅42。'
    '每例5秒为全部K=1…7共享的墙钟预算，包含内部热启动、K构造、搜索、精修及内部审计；'
    '输入读取和末尾独立复核不计入这5秒。最大迭代数设为10^9，避免迭代上限提前停止，其他使用默认策略。'
    '每例使用新进程，串行执行，无历史方案输入，不调用精确求解实现。', '',
    'GAP=(ALNS费用−已证明最优费用)/已证明最优费用，平均值为九例逐例GAP的算术平均。'
    '最优参考只在所有工作进程结束后读取，用于离线评分，不参与初解、选择或停止。'
    '命中按绝对费用差≤10^-6判定；ALNS本身仍不输出最优性证明。', '',
    f"函数实际返回用时为{min(row['wall_seconds'] for row in data['rows']):.4f}—{data['max_wall_seconds']:.4f}秒，"
    f"平均{data['mean_wall_seconds']:.4f}秒。构造与审计等操作之间检查截止时间，因此存在轻微超时。"
    f"仅取调用开始后5.000秒内已记录的可行解，平均GAP仍为{data['mean_gap_recorded_within_5s_percent']:.4f}%，"
    f"命中数仍为{data['known_optima_recorded_within_5s']}。计时口径不会改变本轮结论。", '',
    '时点来自原生局部及跨K改进历史。驱动只转发调度器的真实perf_counter并记录其首次值，'
    '以将局部历史对齐到公开调用起点，不使用参考最优值作为计时或搜索条件。历史记录时点可能晚于实际发现，'
    '因此首次记录是首次发现时刻的保守上界。本轮没有任何命中，不涉及边界命中的归属争议。', '',
    '## 逐例结果', '',
    '| 算例 | 已知最优费用 | 5秒ALNS费用 | GAP | K/N | 达到已知最优 |',
    '| --- | ---: | ---: | ---: | ---: | --- |']
for row in data['rows']:
    lines.append(f"| {row['case']} | {row['exact_objective']:.6f} | {row['objective']:.6f} | "
                 f"{row['gap_percent']:.4f}% | {row['K']}/{row['N']} | {'是' if row['known_optimum_recorded_within_5s'] else '否'} |")
lines += ['', '## 为什么给5秒仍找不到', '',
    '当前搜索把时间分散给多个K。多批次还需要构造分批、边界、回收截点和路线，初期费用并不代表这一结构优化后的水平。'
    '若最优结构的粗糙初解费用偏高，后续精修会转向另一个K。', '']
for case in ['S02', 'S03']:
    row = next(item for item in diagnostics if item['case'] == case)
    lines.append(f"- {case}真正最优为K={row['optimal_K']}，本次该K合计只分到{row['optimal_K_allocated_seconds']:.3f}秒，"
                 f"执行{row['optimal_K_iterations']}次迭代；最终选择K={row['ALNS_K']}。")
lines += ['',
    '此外，选对K和箱池也不等于排好订单、日历和路线。S09本轮选择K=3、N=3，与最优方案一致，'
    '费用仍为49.110344，高于最优43.606813。前序诊断也已验证：正确分批和日历下，现有构造器仍可能产生昂贵路线。'
    '可行修复率改善并没有自动转化成5秒命中最优的能力。当前小规模ALNS的构造、联合邻域和调度仍有明显短板。', '',
    '## 必须纠正“精确求解几秒”的比较口径', '',
    '**此前那轮九例精确计时全部使用了历史可行方案，而且9/9的初始上界已经等于最终最优值。** '
    '这些时间主要衡量从已有方案出发重新构造域、建立有效界并证明最优性，而不是完全冷启动找到该最优方案的时间。'
    '把它概括成“几秒从零找到最优解”不准确，不能直接与本轮冷启动ALNS计算速度倍数。', '',
    '| 算例 | 当时精确计时/秒 | 初始费用 | 最终最优费用 | 初始已最优 |',
    '| --- | ---: | ---: | ---: | --- |']
for row in references:
    lines.append(f"| {row['case']} | {row['wall_seconds']:.4f} | {row['initial_U']:.6f} | {row['final_optimum']:.6f} | 是 |")
lines += ['', f"那轮合计{sum(row['wall_seconds'] for row in references):.4f}秒，"
          f"有{sum(row['wall_seconds'] <= 5. for row in references)}/9例计时不超过5秒，最大{max(row['wall_seconds'] for row in references):.4f}秒。"
          '证明仍是全K域的最优性证明，但起点和本轮ALNS不同。', '',
          '即使排除计时口径差异，本轮平均GAP6.88%、0/9命中仍说明当前ALNS在这组小例上偏弱。'
          '这些数据不能用“启发式本来就不保证最优”一语带过；它们指出了当前实现需要继续改进的具体方向。', '',
          '## 证据与限制', '',
          '本轮仅一个根种子，不能代表多种子稳定性。未修改生产算法，也没有重跑更多种子或挑选更好的一次结果。'
          '运行前预检通过解释器、依赖、Gurobi11.0.3许可证初始化和实际整数优化；此次九例性能任务本身只调用ALNS。', '',
          '`plan.json`固定源代码、输入、预算及驱动SHA-256；`source/`为实际执行的模型源码。'
          '`runs/`含原始输出、独立审计、全程改进历史和逐例摘要；`comparison.json`含逐例及汇总GAP。'
          '`reference_timing_audit.json`核对旧精确初始上界与计时；`K_budget_diagnosis.json`保留最优K的本次分配。'
          '`frozen_inputs/`为输入副本；`evidence_manifest.json`为证据校验表。']
(HERE/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
for path, digest in plan['input_hashes'].items():
    original = ROOT/path
    assert sha(original) == digest
    target = HERE/'frozen_inputs'/original.relative_to(ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(original, target)
    assert sha(target) == digest
files = {path.relative_to(HERE).as_posix(): sha(path) for path in HERE.rglob('*')
         if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc' and path.name != 'evidence_manifest.json'}
save(HERE/'evidence_manifest.json', dict(files=files, root_seeds=[42], source_matches_published_rescue=True))
assert all(sha(HERE/path) == digest for path, digest in files.items())
print(dict(verified_evidence_files=len(files), mean_gap_percent=data['mean_gap_percent'],
           known_optima_within_5s=data['known_optima_recorded_within_5s'],
           exact_initially_optimal=sum(row['initial_already_optimal'] for row in references)))
