"""Verify frozen before/after evidence and write an honest performance report."""
import csv
import json
import re
import xml.etree.ElementTree as ET

from study import OUT, ROOT, read, save, sha, verify


def number(value, digits=3):
    return '—' if value is None else f'{value:.{digits}f}'


def native_points(row):
    phase = next(p for p in row['native_phases'] if p['phase'] == 'full_model')
    path = OUT/'runs'/row['id']/phase['logfile']
    content = path.read_text(encoding='utf-8-sig')
    root = re.search(r'Root relaxation: objective ([\deE+.\-]+)', content)
    points = []
    for line in content.splitlines():
        fields = line.split()
        if fields and fields[0] in ('H', '*'):
            fields = fields[1:]
        if len(fields) < 6 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        gap_index = next((i for i, token in enumerate(fields) if token.endswith('%')), None)
        if gap_index is None:
            continue
        try:
            points.append(dict(seconds=float(fields[-1].removesuffix('s'))+phase['begins_after_seconds'],
                incumbent=float(fields[gap_index-2]), bound=float(fields[gap_index-1])))
        except ValueError:
            continue
    return dict(root_lp=float(root.group(1)) if root else None, points=points)


if __name__ == '__main__':
    plan = read(OUT/'plan.json')
    verify(plan)
    assert read(OUT/'progress.json')['status'] == 'COMPLETE'
    rows = read(OUT/'rows.json')
    suite = ET.parse(OUT/'validation.xml').getroot().find('testsuite')
    assert suite is not None and int(suite.get('tests')) == 135
    assert all(int(suite.get(key)) == 0 for key in ('failures', 'errors', 'skipped'))
    actual = []
    for row in rows:
        target = OUT/'runs'/row['id']
        for filename, expected in read(target/'completion.json')['files'].items():
            assert sha(target/filename) == expected
        if row.get('conditional_job_not_run'):
            assert read(OUT/'runs'/row['only_if_unproven']/'row.json')['optimal']
            continue
        actual.append(row)
        info = read(target/'result.json')['info']
        config = info['solver_config']
        assert row['returncode'] == 0 and config['threads'] == 1 and config['seed'] == 42
        assert config['formulation'] == 'compact' and not row['history_used']
        assert row['budget'] == config['time_limit']
        expected_root = OUT/'before' if row['version'] == 'before' else ROOT
        assert row['loaded_solver_source'] == str(expected_root/'models/routing/model_3/exact/milp_solver.py')
        if row['version'] == 'after':
            assert row['resource_flows'] and config['formulation_options']['precedence_links']
        if row['feasible']:
            audit = read(target/'independent_audit.json')
            assert audit['valid'] and audit['periodic_replay']['valid']
            assert abs(audit['objective']-row['objective']) < 1e-6
        if row['optimal']:
            assert row['feasible'] and row['native_phases'][-1]['status'] == 2
            assert abs(row['bound']-row['objective']) < 1e-6
            assert info['proof_scope'] == 'full_model_3_integer_time_MILP'
    before = next(row for row in actual if row['id'] == 'M03_before_s42_300s')
    after = next(row for row in actual if row['id'] == 'M03_after_s42_300s')
    regular = [row for row in actual if row['version'] == 'after' and row['budget'] == 300]
    assert len(regular) == 19
    confirmation = next((row for row in actual if row['budget'] == 1800), None)
    previous = ROOT/'output/experiments/m3_all_medium_exact_1800s_20261029_v1'
    old_medium = {r['case']: r for r in read(previous/'rows.json')}
    old_small = {r['case']: r for r in read(ROOT/'output/experiments/m3_small_before_after_300s_20261029_v1/rows.json')
                 if r['version'] == 'before'}
    old_large = next(r for r in read(ROOT/'output/experiments/m3_general_acceleration_20261029_v1/rows.json') if r['case'] == 'L01')
    references = dict(**old_small, **old_medium, L01=old_large)
    for row in regular:
        reference = references[row['case']]
        if reference['optimal'] and row['optimal']:
            assert abs(reference['objective']-row['objective']) < 1e-6, row['case']
        if reference['optimal'] and row['feasible']:
            assert row['objective'] >= reference['objective']-1e-6, row['case']
    same_input_files = 0
    for relative, expected in plan['before_hashes'].items():
        if relative.startswith('data/') and relative in plan['hashes']:
            assert plan['hashes'][relative] == expected
            same_input_files += 1
    assert same_input_files >= 70
    traces = {row['id']: native_points(row) for row in (before, after)}
    save(OUT/'paired_M03.json', dict(before=before, after=after, traces=traces,
        optional_confirmation=confirmation, historical_1800s_reference=old_medium['M03']))
    validation = dict(tests=135, passed=135, failed=0, errors=0, skipped=0,
        selected_scope=['test_model3_network_cuts.py', 'test_model3_resource_flows.py',
            'test_model3_route_partition.py', 'test_model3_cost_objective.py', 'test_milp_baseline.py'],
        frozen_actual_calls=len(actual), audited_feasible=sum(row['feasible'] for row in actual),
        optimal=sum(row['optimal'] for row in actual),
        baseline_source_import_verified=True, source_hashes_verified=True,
        historical_input_hash_comparisons=same_input_files,
        regular_candidate_cases=19, regular_candidate_optimal=sum(row['optimal'] for row in regular))
    save(OUT/'validation.json', validation)
    lines = ['# 模型3通用网络加强：冻结对照与跨规模验证', '',
        '本次实现：配送/回收弧流替代二级大M载荷传播；添加原因果关系导出的弧冲突与提升时间联系；'
        '在准备预算内分离有效连通/容量子集并加入完整主模型；改进当前输入生成的初解。'
        '不按算例或顾客数设置开关，业务数据、费用、车队及整数时钟保持原定义。', '',
        '## M03同预算正式对照', '',
        '两次独立进程按相同输入、单线程、种子42、300秒总预算运行。'
        '优化前从旧版冻结源码包导入，包含此前通用加速；不是最初的expanded冷启动。'
        '优化后从本次冻结源码导入，不把旧结果、路线或最优值传给求解器。', '',
        '| 版本 | 总秒数 | 状态 | 可行成本 | 原生全局下界 | GAP | 初始根LP |',
        '| --- | ---: | --- | ---: | ---: | ---: | ---: |']
    for label, row in (('优化前', before), ('优化后', after)):
        gap = '—' if row['gap'] is None else f'{100*row["gap"]:.4f}%'
        lines.append(f'| {label} | {number(row["total_seconds"])} | {row["status"]} | '
            f'{number(row["objective"], 6)} | {number(row["bound"], 6)} | {gap} | '
            f'{number(traces[row["id"]]["root_lp"], 6)} |')
    if confirmation is not None:
        lines += ['', '300秒未证明最优时，按预先登记方案另开一次独立1800秒确认；'
            '这是新建模型的运行，不是接续300秒搜索状态。', '',
            f'确认结果：{confirmation["status"]}，总耗时{confirmation["total_seconds"]:.3f}秒，'
            f'可行成本{number(confirmation["objective"], 6)}，下界{number(confirmation["bound"], 6)}，'
            f'GAP {100*confirmation["gap"]:.4f}%。', '',
            '旧版已有1800秒历史记录：可行成本131.848713、下界129.785251、GAP 1.5650%。'
            '这条历史记录没有在本次重新执行；正式同预算新对照见上表。']
    else:
        lines += ['', '优化后300秒内已证明最优，预先登记的1800秒确认无需执行。']
    lines += ['', '## 相同默认版本的跨规模结果', '',
        '以下19例统一300秒预算、单线程、种子42，无规模专用参数。', '',
        '| 算例 | 单数 | 总秒数 | 状态 | 成本 | 下界 | GAP |',
        '| --- | ---: | ---: | --- | ---: | ---: | ---: |']
    for row in sorted(regular, key=lambda r: (r['orders'], r['case'])):
        gap = '—' if row['gap'] is None else f'{100*row["gap"]:.4f}%'
        lines.append(f'| {row["case"]} | {row["orders"]} | {number(row["total_seconds"])} | '
            f'{row["status"]} | {number(row["objective"], 6)} | {number(row["bound"], 6)} | {gap} |')
    lines += ['', '总时间计入案例加载、新建模型、下界/切割/初解准备、完整主模型求解、提取与独立审计。'
        '解释器启动、版本哈希核对和环境预检另计。达到TIME_LIMIT不能称为已得到精确最优解。', '',
        '## 其他已最优中规模的历史耗时参考', '',
        '以下旧时间来自先前单种子42、单线程、1800秒上限的已完成记录；新时间为本次300秒上限运行。'
        '只有两边均证明最优时才可比较完整证明时间。该表没有重新执行旧版，不能当作新配对实验。', '',
        '| 算例 | 原总秒数 | 新总秒数 | 新状态 |',
        '| --- | ---: | ---: | --- |']
    for row in regular:
        if row['size'] == 'medium' and row['case'] != 'M03':
            lines.append(f'| {row["case"]} | {old_medium[row["case"]]["total_seconds"]:.3f} | '
                         f'{row["total_seconds"]:.3f} | {row["status"]} |')
    lines += ['', '容易实例也可能因额外准备或更大的流模型增加耗时；单例退化会如实保留，'
        '不据此再按顾客数切换算法。', '',
        '## 正确性、范围与证据', '',
        '- 选定5个相关测试文件：135通过、0失败、0错误、0跳过；不是整个项目全套测试。',
        '- 全部可行结果重新检查成本和连续两周期。只有完整业务MILP原生OPTIMAL且审计通过时才标为最优。',
        '- `source_before.zip`、`source_after.zip`与`plan.json`保留版本、输入哈希、固定种子和实际预算；'
        '每个作业前后均核对冻结文件。',
        '- `runs/*/`保留所有辅助LP、内部调度和完整主模型的独立日志、原结果、审计及完成文件哈希。',
        '- `runs.csv`、`rows.json`与`paired_M03.json`记录全部结果，包含未证明最优的运行。',
        '- 单种子结果不表示已验证多种子稳定性；加强下界也不保证所有实例都更快。', '',
        '有效性与等价性推导见 `docs/model3_network_strengthening.md`。', '']
    (OUT/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for row, color, label in ((before, '#6b7280', 'Before'), (after, '#0e7490', 'After')):
        points = traces[row['id']]['points']
        if points:
            axes[0].step([p['seconds'] for p in points], [p['incumbent'] for p in points],
                         where='post', color=color, label=label)
            axes[1].step([p['seconds'] for p in points], [p['bound'] for p in points],
                         where='post', color=color, label=label)
    for axis, title in zip(axes, ('M03 native feasible objective progress', 'M03 native global lower bound')):
        axis.set(xlabel='Total elapsed seconds (preparation included)', ylabel='Total cost', title=title, xlim=(0, 300))
        axis.grid(alpha=.2)
        axis.legend()
    fig.tight_layout()
    fig.savefig(OUT/'M03_before_after.png', dpi=160)
    plt.close(fig)
    files = [p for p in OUT.rglob('*') if p.is_file() and '__pycache__' not in p.parts
             and p.name != 'evidence_manifest.json' and not p.is_relative_to(OUT/'before')]
    save(OUT/'evidence_manifest.json', dict(files={p.relative_to(OUT).as_posix(): sha(p) for p in files}))
    print(json.dumps(validation, ensure_ascii=False), flush=True)
