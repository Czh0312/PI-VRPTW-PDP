"""Check frozen evidence and write a factual cross-size report."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from study import OUT, ROOT, read, save, sha, verify


def fmt(value, digits=3):
    return '—' if value is None else f'{value:.{digits}f}'


if __name__ == '__main__':
    plan = read(OUT/'plan.json')
    verify(plan)
    rows = read(OUT/'rows.json')
    assert len(rows) == 13 and read(OUT/'progress.json')['status'] == 'COMPLETE'
    suite = ET.parse(OUT/'validation.xml').getroot().find('testsuite')
    assert suite is not None and int(suite.get('tests')) == 119
    assert all(int(suite.get(key)) == 0 for key in ('errors', 'failures', 'skipped'))
    old_small_dir = ROOT/'output/experiments/m3_small_before_after_300s_20261029_v1'
    old_m01_dir = ROOT/'output/experiments/m3_m01_exact_300s_20261029_v1'
    old_m23_dir = ROOT/'output/experiments/m3_medium_m02_m03_20261029_v1'
    old_small = {r['case']: r for r in read(old_small_dir/'rows.json') if r['version'] == 'before'}
    old_m01 = read(old_m01_dir/'summary.json')
    old_m23 = {r['case']: r for r in read(old_m23_dir/'rows.json')}
    comparisons = []
    for row in rows:
        # Windows venv python.exe redirects to a child interpreter. The
        # driver's root-PID RSS is a launcher measurement, not solver RSS.
        row['launcher_peak_rss_mib'] = row.get('launcher_peak_rss_mib', row['peak_rss_mib'])
        row['peak_rss_mib'] = None
        row['memory_measurement'] = 'launcher_only_solver_peak_unavailable'
        target = OUT/'runs'/row['id']
        completion = read(target/'completion.json')
        for filename, expected in completion['files'].items():
            assert sha(target/filename) == expected
        result = read(target/'result.json')
        info = result['info']
        config = info['solver_config']
        assert config['seed'] == 42 and config['threads'] == 1 and config['formulation'] == 'compact'
        assert row['bound_applied'] and config['formulation_options']['capacity_links']
        assert not info['route_strengthening']['historical_information_used']
        if row['feasible']:
            audit = read(target/'independent_audit.json')
            assert audit['valid'] and audit['periodic_replay']['valid']
            assert abs(audit['objective']-row['objective']) < 1e-6
        if row['optimal']:
            assert row['feasible'] and row['native_phases'][-1]['status'] == 2
            assert info['proof_scope'] == 'full_model_3_integer_time_MILP'
            assert abs(row['bound']-row['objective']) < 1e-6
        previous = None
        if row['size'] == 'small':
            previous = old_small[row['case']]
            assert row['optimal'] and abs(previous['objective']-row['objective']) < 1e-6
            historical_time = previous['total_seconds']
        elif row['case'] == 'M01':
            previous = old_m01
            historical_time = previous['total_wall_seconds']
        elif row['case'] in old_m23:
            previous = old_m23[row['case']]
            historical_time = previous.get('total_wall_seconds', previous['runtime'])
        if previous is not None:
            if previous['objective'] is not None and row['optimal']:
                assert abs(previous['objective']-row['objective']) < 1e-6
            comparisons.append(dict(case=row['case'], old_seconds=historical_time,
                new_seconds=row['total_seconds'], old_status=previous['status'], new_status=row['status'],
                old_objective=previous['objective'], new_objective=row['objective'],
                speedup_if_both_optimal=historical_time/row['total_seconds'] if row['optimal'] and previous['status'] == 'OPTIMAL' else None))
    # Historical results are read only for reporting, after all new solves.
    checked = 0
    for old_plan_path, hash_key in ((old_small_dir/'plan.json', 'hashes'),
            (old_m01_dir/'plan.json', 'hashes'), (old_m23_dir/'plan.json', 'source_hashes_and_inputs')):
        for relative, expected in read(old_plan_path)[hash_key].items():
            if relative.startswith('data/') and relative in plan['hashes']:
                assert plan['hashes'][relative] == expected, relative
                checked += 1
    assert checked >= 80
    save(OUT/'rows.json', rows)
    with (OUT/'runs.csv').open('r', encoding='utf-8-sig', newline='') as stream:
        fields = list(csv.DictReader(stream).fieldnames)
    for field in ('launcher_peak_rss_mib', 'memory_measurement'):
        if field not in fields:
            fields.append(field)
    with (OUT/'runs.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    save(OUT/'driver_observation.json', dict(
        issue='Root-PID RSS belongs to the Windows venv launcher; the solver runs in its child.',
        diagnostic='memory_diagnostic.log', solver_peak_memory_measured=False,
        correction='Keep raw process.json unchanged; aggregate solver peak RSS is null. Launcher RSS is separately labelled.',
        frozen_solver_or_driver_changed=False, solver_times_and_native_bounds_affected=False))
    save(OUT/'historical_comparison.json', comparisons)
    validation = dict(selected_tests=119, passed=119, failed=0, errors=0, skipped=0,
        selected_scope=['tests/routing/test_model3_route_partition.py',
                        'tests/routing/test_model3_cost_objective.py',
                        'tests/routing/test_milp_baseline.py'],
        frozen_runs=13, frozen_hashes_valid=True, historical_input_hash_comparisons=checked,
        solver_peak_memory_measured=False,
        independent_feasible_audits=sum(r['feasible'] for r in rows),
        full_model_optimal_proofs=sum(r['optimal'] for r in rows))
    save(OUT/'validation.json', validation)
    small = [r for r in rows if r['size'] == 'small']
    lines = ['# 模型3：同一通用优化跨规模验证', '',
        '同一份冻结代码、同一个默认入口 `solve_exact(p, time_limit=300, seed=42)`，'
        '固定单线程、单进程顺序运行。覆盖9个7单、3个20单和1个50单算例；没有按规模手动调参。', '',
        '默认共享内部弧，加入原容量推导的载荷/启用联系及必要趟数；每仓先求连续弧覆盖下界，'
        '再按统一准备预算尝试完整车次枚举。未完成仓保留有效下界，部分车次只作初解候选。'
        '业务日历、分仓、整数时钟、箱池和成本口径均保持原定义。', '',
        '下界准备和初解准备各最多使用当时剩余预算25%，且各不超过5秒，均从总预算扣除。'
        '枚举50万前缀是统一工作量保护，不限制原可行路线或顾客数。', '',
        '## 结果', '',
        '| 算例 | 单数 | 端到端秒数 | 状态 | 校验可行成本 | 完整模型下界 | GAP | 二元变量 |',
        '| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |']
    for row in rows:
        gap = '—' if row['gap'] is None else f'{100*row["gap"]:.3f}%'
        lines.append(f'| {row["case"]} | {row["orders"]} | {fmt(row["total_seconds"])} | '
                     f'{row["status"]} | {fmt(row["objective"], 6)} | {fmt(row["bound"], 6)} | {gap} | {row["binary"]} |')
    lines += ['', f'9个小规模全部证明最优，与原成本逐例一致；合计{sum(r["total_seconds"] for r in small):.3f}秒，'
        f'单例{min(r["total_seconds"] for r in small):.3f}–{max(r["total_seconds"] for r in small):.3f}秒。', '',
        '表中秒数含案例读取、新下界、新初解、完整模型求解、提取及独立校验；进程启动和运行时预检另存。'
        'TIME_LIMIT只表示预算内停止，不能称为得到精确最优解；有可行成本时才可报告GAP。', '',
        '## 已有结果的历史参考', '',
        '下列原结果使用同输入、种子42、单线程、300秒预算，其输入哈希已核对。'
        '这些是已有记录与新默认版本的对照，并非重新交替执行的成对基准；单种子结果不代表多种子稳定性。', '',
        '| 算例 | 原秒数 | 新秒数 | 原状态 | 新状态 | 同为最优时速度比 |',
        '| --- | ---: | ---: | --- | --- | ---: |']
    for row in comparisons:
        lines.append(f'| {row["case"]} | {fmt(row["old_seconds"])} | {fmt(row["new_seconds"])} | '
                     f'{row["old_status"]} | {row["new_status"]} | {fmt(row["speedup_if_both_optimal"], 2)} |')
    lines += ['', '通用加强不保证每例都更快。容易实例可能因准备工作增加耗时；'
        '大实例仍可能未在预算内证明最优。此次没有据此给个别规模另设算法或参数。', '',
        '## 证据', '',
        '- `plan.json`、`source_inputs.zip`：实际入口、预算、版本及全部源文件/输入哈希。',
        '- `runs.csv`、`rows.json`：13次结果、准备/主求解时间、模型规模及进程耗时。',
        '- `runs/*/`：每个LP、完整划分、副本调度和完整主模型的独立原生日志、完整结果及重新运行的审计。',
        '- `validation.xml`、`validation.json`：选定3个相关测试文件，119通过、0失败、0错误、0跳过；不是全套测试。',
        '- `historical_comparison.json`：对照原记录，实验求解本身未使用历史最优值或方案。', '',
        '内存采样只覆盖Windows虚拟环境启动器，未测得实际求解进程峰值；汇总峰值内存字段留空，'
        '启动器RSS单独标记。原始 `process.json` 保留，测量范围说明见 `driver_observation.json`，'
        '现场验证见 `memory_diagnostic.log`。求解时间及原生上下界不受影响。', '',
        '只有完整原主模型原生OPTIMAL并通过独立成本与两周期审计时才记为最优；'
        'LP最优、完整放松划分最优或候选调度副本最优均不单独构成原问题最优证明。', '',
        '数学有效性与回退规则详见 `docs/model3_general_acceleration.md`。', '']
    (OUT/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    manifest_files = [p for p in OUT.rglob('*') if p.is_file() and p.name != 'evidence_manifest.json'
                      and '__pycache__' not in p.parts]
    save(OUT/'evidence_manifest.json', dict(files={p.relative_to(OUT).as_posix(): sha(p) for p in manifest_files}))
    print(json.dumps(validation, ensure_ascii=False), flush=True)
