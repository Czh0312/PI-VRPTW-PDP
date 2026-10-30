"""Verify all nine independent medium runs and report proof times honestly."""
from __future__ import annotations

import json
from pathlib import Path
import xml.etree.ElementTree as ET

from study import OUT, ROOT, now, read, save, sha, verify


def number(value, digits=3):
    return '—' if value is None else f'{value:.{digits}f}'


def finalize():
    plan = read(OUT/'plan.json')
    verify(plan)
    rows = read(OUT/'rows.json')
    assert len(rows) == len(plan['jobs']) == 9
    assert {row['case'] for row in rows} == {f'M{i:02}' for i in range(1, 10)}
    assert read(OUT/'progress.json')['status'] == 'COMPLETE'
    for row in rows:
        target = OUT/'runs'/row['id']
        completion = read(target/'completion.json')
        for filename, expected in completion['files'].items():
            assert sha(target/filename) == expected, (row['case'], filename)
        result = read(target/'result.json')
        info = result['info']
        config = info['solver_config']
        assert row['seed'] == config['seed'] == 42 and row['budget'] == config['time_limit'] == 1800
        assert config['formulation'] == 'compact' and config['threads'] == 1
        assert not row['history_used']
        assert row['orders'] == 20
        if row['feasible']:
            audit = read(target/'independent_audit.json')
            assert audit['valid'] and audit['periodic_replay']['valid']
            assert abs(audit['objective']-row['objective']) < 1e-6
        if row['optimal']:
            assert row['feasible'] and row['native_phases'][-1]['status'] == 2
            assert info['proof_scope'] == 'full_model_3_integer_time_MILP'
            assert abs(row['bound']-row['objective']) < 1e-6 and row['proof_seconds'] is not None
        else:
            assert row['proof_seconds'] is None
        assert row['peak_process_tree_rss_mib'] >= row['launcher_peak_rss_mib']
        assert row['maximum_process_count'] >= 2
    previous = ROOT/'output/experiments/m3_general_acceleration_20261029_v1'
    previous_rows = {row['case']: row for row in read(previous/'rows.json') if row['size'] == 'medium'}
    for row in rows:
        old = previous_rows.get(row['case'])
        if old and old['optimal'] and row['optimal']:
            assert abs(old['objective']-row['objective']) < 1e-6
    # Reuse only validation evidence, never a previous solution/bound in a solve.
    suite = ET.parse(previous/'validation.xml').getroot().find('testsuite')
    assert suite is not None and int(suite.get('tests')) == 119
    assert all(int(suite.get(key)) == 0 for key in ('errors', 'failures', 'skipped'))
    optimal = [row for row in rows if row['optimal']]
    feasible = [row for row in rows if row['feasible']]
    timeout = [row for row in rows if row['status'].startswith('TIME_LIMIT')]
    statistics = dict(cases=9, orders_per_case=20, seed=42, threads=1, budget_seconds=1800,
        optimal=len(optimal), feasible=len(feasible), timeout=len(timeout),
        proven_infeasible=sum(row['infeasible'] for row in rows),
        total_solver_and_audit_seconds=sum(row['total_seconds'] for row in rows),
        fastest_optimal_seconds=min((row['total_seconds'] for row in optimal), default=None),
        slowest_optimal_seconds=max((row['total_seconds'] for row in optimal), default=None),
        finished_at=now())
    save(OUT/'statistics.json', statistics)
    validation = dict(frozen_source_and_input_hashes_valid=True,
        source_matches_previously_validated_version=True,
        independent_feasible_audits=len(feasible), complete_model_optimal_proofs=len(optimal),
        selected_regression_tests_rerun_this_experiment=False,
        reused_regression_evidence=dict(path=previous.relative_to(ROOT).as_posix()+'/validation.xml',
            tests=119, passed=119, failures=0, errors=0, skipped=0,
            scope=['tests/routing/test_model3_route_partition.py',
                   'tests/routing/test_model3_cost_objective.py', 'tests/routing/test_milp_baseline.py']),
        no_solver_or_model_changes_this_experiment=True)
    save(OUT/'validation.json', validation)
    lines = ['# 模型3全部中规模：统一1800秒精确求解', '',
        '范围为正式数据集的M01–M09，共9例，每例20单。固定当前优化版本、种子42、'
        '单线程、单进程顺序运行。所有实例使用 `solve_exact(p, time_limit=1800, seed=42)`；'
        '完整主模型证明最优后立即结束，不按算例修改模型或求解参数。', '',
        f'结果：{len(optimal)}/9例证明最优，{len(feasible)}/9例得到通过独立审计的可行解，'
        f'{len(timeout)}/9例达到时间上限。', '',
        '| 算例 | 仓内订单分布 | 证明最优耗时/秒 | 含校验总耗时/秒 | 状态 | 成本 | 下界 | GAP |',
        '| --- | --- | ---: | ---: | --- | ---: | ---: | ---: |']
    for row in rows:
        warehouses = ', '.join(f'{site}:{count}' for site, count in sorted(row['warehouse_orders'].items()))
        gap = '—' if row['gap'] is None else f'{100*row["gap"]:.4f}%'
        lines.append(f'| {row["case"]} | {warehouses} | {number(row["proof_seconds"])} | '
            f'{number(row["total_seconds"])} | {row["status"]} | {number(row["objective"], 6)} | '
            f'{number(row["bound"], 6)} | {gap} |')
    lines += ['', '“证明最优耗时”从案例加载开始，计入新建模型、输入型下界、内部初解及完整主模型求解，'
        '结束于完整主模型返回原生OPTIMAL；随后还必须通过独立业务审计。'
        '“含校验总耗时”另含提取、独立成本与两周期审计。Python进程启动、输入/源哈希核对和运行时预检另计。', '',
        'TIME_LIMIT行未测得证明最优所需时间，表内留空；不能把1800秒当作求到精确最优解的时间。'
        '预算包含准备工作，求解器停止与提取校验可能使墙钟总时略超预算。', '',
        '## 校验与复现', '',
        '- `plan.json`、`source_inputs.zip`冻结实际代码、数据与配置哈希；全部作业前后均复核。',
        '- `runs.csv`、`rows.json`记录9例耗时、原生状态、上下界、审计、模型规模和进程树RSS采样。',
        '- 每例目录内保留各辅助LP、完整划分、初解调度副本和完整主模型的独立原生日志。'
        '只有最后的完整主模型OPTIMAL并通过审计才算证明最优。',
        '- 每次调用均从当前输入重新构建；不读取历史方案、历史最优值或上一例解作为初解。',
        '- 本次未改求解器或数学模型，源哈希与先前验证版一致；沿用已完成的3个相关测试文件、'
        '119通过/0失败/0错误/0跳过的回归证据。本次没有重新运行这些测试，也没有声称全套测试通过。', '',
        '这里只运行单一种子42。时间对照限于当前机器、当前实现和该种子，不代表多种子统计稳定性。', '']
    (OUT/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    files = [p for p in OUT.rglob('*') if p.is_file() and p.name != 'evidence_manifest.json'
             and '__pycache__' not in p.parts]
    save(OUT/'evidence_manifest.json', dict(files={p.relative_to(OUT).as_posix(): sha(p) for p in files}))
    print(json.dumps(statistics, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    finalize()
