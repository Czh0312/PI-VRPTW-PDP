"""Check experiment artifacts and add the completed representative matrix."""
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    manifest = read(OUT/'manifest.json')
    rows = read(OUT/'summary.json')
    expected = {(case, model) for case in ('S01', 'S04', 'S07') for model in range(5, 9)}
    assert manifest['completed'] == manifest['planned'] == len(rows) == 12
    assert {(row['case'], row['model']) for row in rows} == expected
    source_checks = input_checks = 0
    for row in rows:
        run = OUT/row['run_dir']
        raw = read(run/'result.json')
        evidence = read(run/'manifest.json')
        info, audit = raw['info'], raw.get('audit', {})
        options = info['solver_config']
        assert options['time_limit'] == 300 and options['threads'] == 1
        assert options['formulation'] == 'expanded' and not options['mip_start']
        assert not options['custom_callback'] and not options['external_certificate']
        assert raw['model'] == row['model'] and raw['solver'] == 'exact'
        assert info['status'] == row['status']
        assert info.get('objective') == row.get('objective')
        if row['model'] >= 6:
            case_config = read(ROOT/f"data/formal_benchmark27_v1/model_{row['model']}/small/{row['case']}/case.json")
            assert info['batch_count_candidates'] == case_config['joint_batch_options']['batch_count_candidates']
            assert info['exact_strategy'] == 'gurobi_unified_milp'
        if row['valid']:
            assert audit['valid'] and audit['periodic_replay']['valid']
        if row['proven_optimal']:
            assert row['valid'] and info['proven_optimal'] and info['status'] == 'OPTIMAL'
        for name, expected_hash in evidence['source_sha256'].items():
            assert hashlib.sha256((run/'source_snapshot'/name).read_bytes()).hexdigest() == expected_hash
            assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == expected_hash
            source_checks += 1
        for name, expected_hash in evidence['input_sha256'].items():
            assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == expected_hash
            input_checks += 1
    witness = read(OUT/'witness_audit.json')
    assert witness['valid'] and len(witness['rows']) == 12
    assert all(row['valid'] and row['periodic_replay_valid'] for row in witness['rows'])
    checks = dict(valid=True, exact_runs=12, optimal=sum(row['proven_optimal'] for row in rows),
                  audited_feasible=sum(row['valid'] for row in rows),
                  feasible_unproven=sum(row['valid'] and not row['proven_optimal'] for row in rows),
                  no_audited_incumbent=sum(not row['valid'] for row in rows),
                  proven_infeasible=sum(row['proven_infeasible'] for row in rows),
                  business_witnesses_passed=12, source_hash_checks=source_checks, input_hash_checks=input_checks)
    (OUT/'final_checks.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
    matrix = ['## 代表例实跑结论', '',
              '| 算例 | 顾客 | M5 | M6 | M7 | M8 |', '| --- | ---: | --- | --- | --- | --- |']
    lookup = {(row['case'], row['model']): row for row in rows}
    for case, customers in (('S01', 7), ('S04', 10), ('S07', 12)):
        cells = []
        for model in range(5, 9):
            row = lookup[case, model]
            if row['proven_optimal']:
                seconds = (row.get('build_time') or 0)+(row.get('mip_time') or 0)
                cells.append(f'已证明最优，{seconds:.1f} 秒')
            elif row['valid']:
                gap = '未知' if row.get('gap') is None else f"{100*row['gap']:.2f}%"
                cells.append(f'限时可行，gap {gap}')
            elif row['status'] == 'TIME_LIMIT':
                cells.append('300 秒未找到可行解')
            elif row['status'] == 'TIME_LIMIT_BUILD':
                cells.append('300 秒内未完成完整建模')
            else:
                cells.append(row['status'])
        matrix.append(f"| {case} | {customers} | {' | '.join(cells)} |")
    matrix += ['', f"12 次目标运行：{checks['optimal']} 次证明最优、{checks['feasible_unproven']} 次限时可行但未证明最优、"
               f"{checks['no_audited_incumbent']} 次无审计有效解、{checks['proven_infeasible']} 次证明不可行。",
               '上表最优行的时间为建模加 Gurobi 求解，不含启动、输入与源码快照、提取及审计开销。',
               '12 份保存的业务可行调度在当前输入和审计代码下均通过独立两周期重放；它们没有传给精确求解器。',
               '因此，限时未找到解不代表业务数据不可行；本轮仅评价当前入口在本机与指定预算下的表现。',
               '所有选定运行的配置、完整输入 K 域、源码及输入哈希均已核对，详见 final_checks.json。', '']
    path = OUT/'report.md'
    body = path.read_text(encoding='utf-8')
    if '## 代表例实跑结论' not in body:
        first, rest = body.split('\n', 1)
        path.write_text(first+'\n\n'+'\n'.join(matrix)+'\n'+rest, encoding='utf-8')
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == '__main__':
    main()
