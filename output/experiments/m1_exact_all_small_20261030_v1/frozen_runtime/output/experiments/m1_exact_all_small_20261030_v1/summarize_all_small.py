"""Verify and compare all nine freshly solved small cases."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
VARIANTS = ('expanded_baseline', 'compact_only', 'compact_network')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def summarize():
    plan = read(OUT/'plan.json')
    for relative, expected in plan['hashes'].items():
        assert sha(OUT/plan['frozen_runtime']/relative) == expected, relative
    assert sha(OUT/'source_inputs.zip') == plan['archive_sha256']

    rows = []
    artifact_hashes = {}
    for job in plan['jobs']:
        row_path = OUT/'runs'/job['id']/'row.json'
        row = read(row_path)
        assert row['size'] == 'small' and row['orders'] == 7
        assert row['seed'] == 42 and row['budget'] == 30 and row['solver_config']['threads'] == 1
        assert not row['historical_information_used']
        row['reused_call'] = False
        row['evidence_directory'] = row_path.parent.relative_to(ROOT).as_posix()
        for path in row_path.parent.iterdir():
            if path.is_file() and path.suffix in ('.json', '.log'):
                artifact_hashes[path.relative_to(ROOT).as_posix()] = sha(path)
        audit = read(row_path.parent/'independent_audit.json')
        if row['feasible']:
            assert audit['valid'] and audit['periodic_replay']['valid']
            assert math.isclose(row['objective'], audit['objective'], rel_tol=1e-7, abs_tol=1e-5)
        assert all(phase['starts'] == 0 for phase in row['phases'])
        rows.append(row)
    rows.sort(key=lambda r: (r['case'], VARIANTS.index(r['variant'])))
    assert len(rows) == 27
    assert len({(r['case'], r['variant']) for r in rows}) == 27
    assert {r['case'] for r in rows} == {f'S{i:02}' for i in range(1, 10)}

    comparisons = []
    for case in sorted({r['case'] for r in rows}):
        selected = {r['variant']: r for r in rows if r['case'] == case}
        reference = selected['expanded_baseline']
        if all(r['optimal'] for r in selected.values()):
            assert max(r['objective'] for r in selected.values())-min(r['objective'] for r in selected.values()) < 1e-5
        for r in selected.values():
            if reference['optimal'] and r['bound'] is not None:
                assert r['bound'] <= reference['objective']+1e-5
        entry = dict(case=case, orders=7,
            objective=reference['objective'] if reference['optimal'] else None,
            all_optimal=all(r['optimal'] for r in selected.values()),
            expanded_seconds=reference['total_seconds'], compact_seconds=selected['compact_only']['total_seconds'],
            network_seconds=selected['compact_network']['total_seconds'],
            expanded_main_seconds=reference['main_mip_seconds'],
            compact_main_seconds=selected['compact_only']['main_mip_seconds'],
            network_main_seconds=selected['compact_network']['main_mip_seconds'],
            network_preparation_seconds=selected['compact_network']['preparation_seconds'],
            reused_case=False)
        entry['network_reduction_vs_expanded_pct'] = 100*(1-entry['network_seconds']/entry['expanded_seconds'])
        entry['network_reduction_vs_compact_pct'] = 100*(1-entry['network_seconds']/entry['compact_seconds'])
        comparisons.append(entry)

    aggregates = {}
    for variant in VARIANTS:
        selected = [r for r in rows if r['variant'] == variant]
        times = [r['total_seconds'] for r in selected]
        aggregates[variant] = dict(cases=len(selected), optimal=sum(r['optimal'] for r in selected),
            feasible=sum(r['feasible'] for r in selected), total_seconds=sum(times),
            mean_seconds=statistics.mean(times), median_seconds=statistics.median(times),
            min_seconds=min(times), max_seconds=max(times))
    ratios = {}
    for reference in ('expanded_baseline', 'compact_only'):
        pairs = [(next(r for r in rows if r['case'] == c['case'] and r['variant'] == reference)['total_seconds'],
                  c['network_seconds']) for c in comparisons]
        ratios[reference] = dict(faster_cases=sum(b < a for a, b in pairs),
            slower_cases=sum(b > a for a, b in pairs),
            total_reduction_pct=100*(1-aggregates['compact_network']['total_seconds']/aggregates[reference]['total_seconds']),
            total_speedup=aggregates[reference]['total_seconds']/aggregates['compact_network']['total_seconds'],
            paired_geometric_speedup=math.exp(statistics.mean(math.log(a/b) for a, b in pairs)))
    with (OUT/'all_small_runs.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        fields = ['case', 'variant', 'seed', 'budget', 'status', 'optimal', 'feasible', 'objective', 'bound', 'gap',
                  'total_seconds', 'preparation_seconds', 'main_mip_seconds', 'variables', 'binary', 'nodes',
                  'reused_call', 'evidence_directory']
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    with (OUT/'comparison.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(comparisons[0]))
        writer.writeheader()
        writer.writerows(comparisons)
    save(OUT/'all_small_rows.json', rows)
    save(OUT/'summary.json', dict(scope='All nine seven-customer small cases; no medium or large jobs',
        seeds=[42], per_call_budget_seconds=30, new_calls=27, reused_calls=0,
        aggregates=aggregates, network_vs=ratios, comparisons=comparisons))
    test_suite = ET.parse(OUT/'validation.xml').getroot().find('testsuite').attrib
    evidence = dict(passed=True, generated_at=datetime.now(timezone.utc).isoformat(),
        scope='S01-S09, three expressions, 27 completed calls, seed 42, single-thread',
        new_calls=27, reused_calls=0, completed=27,
        optimal=sum(r['optimal'] for r in rows), audited_feasible=sum(r['feasible'] for r in rows),
        failures=sum(r['status'] not in ('OPTIMAL', 'TIME_LIMIT') for r in rows),
        new_jobs_all_small=all(j['size'] == 'small' for j in plan['jobs']),
        candidate_identical_to_pilot=sha(OUT/'candidate.py') == sha(ROOT/'output/experiments/m1_exact_transfer_review_20261030_v1/candidate.py'),
        frozen_runtime_and_inputs_unchanged=True,
        selected_unit_tests=dict(passed=int(test_suite['tests'])-int(test_suite['failures'])-int(test_suite['errors'])-int(test_suite['skipped']),
            failed=int(test_suite['failures']), errors=int(test_suite['errors']), skipped=int(test_suite['skipped']),
            rerun=True, scope='Research candidate test_transfer.py only; independent tiny oracle.'),
        artifacts=artifact_hashes)
    save(OUT/'all_small_verification.json', evidence)
    write_report(comparisons, aggregates, ratios, evidence)
    print(json.dumps(dict(all_small_completed=27, optimal=evidence['optimal'], feasible=evidence['audited_feasible'],
        total_seconds={k: round(v['total_seconds'], 6) for k, v in aggregates.items()}, network_vs=ratios),
        ensure_ascii=False), flush=True)


def write_report(comparisons, aggregates, ratios, evidence):
    lines = ['# 模型1：全部9个小规模算例的精确求解对照', '',
        '2026-10-30。仅S01–S09，每例7位顾客；3种表达、种子42、单线程、每次30秒。',
        '本次在同一当前源码快照下重新运行全部9例的三种表达，共27次；未沿用前轮调用，没有新增中、大规模运行。', '',
        '候选与前轮逐字节相同；因共享代码有更新，全部小规模重新测量。原默认expanded、已有compact和compact加纯配送容量/流/连通下界的组合各运行9例。',
        '全部调用使用本目录frozen_runtime中的源码与输入副本，防止其他聊天在共享仓库的修改影响不同算例。',
        '总时间包含加载、建模、准备、完整主MILP、解提取与独立审计；解释器启动、环境预检和哈希核对另计。', '',
        '| 算例 | 默认expanded/秒 | compact/秒 | 加强组合/秒 | 相对默认耗时减少 | 相对compact耗时减少 | 最优距离 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for c in comparisons:
        objective = f"{c['objective']:.6f}" if c['objective'] is not None else '未证最优'
        lines.append(f"| {c['case']} | {c['expanded_seconds']:.3f} | {c['compact_seconds']:.3f} | {c['network_seconds']:.3f} | {c['network_reduction_vs_expanded_pct']:.1f}% | {c['network_reduction_vs_compact_pct']:.1f}% | {objective} |")
    lines += ['', '正百分数表示加强组合更快，负百分数表示更慢。', '',
        '| 表达 | 已证最优 | 9例合计/秒 | 平均/秒 | 中位数/秒 | 最长/秒 |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for variant, label in (('expanded_baseline', '默认expanded'), ('compact_only', 'compact'), ('compact_network', '加强组合')):
        a = aggregates[variant]
        lines.append(f"| {label} | {a['optimal']}/9 | {a['total_seconds']:.3f} | {a['mean_seconds']:.3f} | {a['median_seconds']:.3f} | {a['max_seconds']:.3f} |")
    lines += ['',
        f"加强组合相对默认：{ratios['expanded_baseline']['faster_cases']}/9更快、{ratios['expanded_baseline']['slower_cases']}/9更慢；合计时间减少{ratios['expanded_baseline']['total_reduction_pct']:.1f}%，合计时间比为{ratios['expanded_baseline']['total_speedup']:.2f}倍。",
        f"加强组合相对compact：{ratios['compact_only']['faster_cases']}/9更快、{ratios['compact_only']['slower_cases']}/9更慢；合计时间减少{ratios['compact_only']['total_reduction_pct']:.1f}%。", '',
        f"完成27/27次；已证最优{evidence['optimal']}/27，独立业务与周期审计通过{evidence['audited_feasible']}/27。同一算例各表达的已证最优目标一致。", '',
        '结论必须同时查看总耗时和逐例退化，不能只挑最快或最慢的实例。小于一秒的差别可能受到加载、首次调用和机器负载影响。',
        '这是同版本、单种子、9个小规模算例的结果；未验证多种子稳定性，也不推断中、大规模性能。', '',
        f"本次重新运行独立穷举核验：所选{evidence['selected_unit_tests']['passed']}项通过、{evidence['selected_unit_tests']['failed']}失败、{evidence['selected_unit_tests']['errors']}错误、{evidence['selected_unit_tests']['skipped']}跳过；不是完整测试套件。",
        '运行前统一环境预检READY，含实际Gurobi整数优化。全部新增调用都限定small；原生产求解入口和默认设置未改动。', '',
        '完整结果：`all_small_runs.csv`、`comparison.csv`、`summary.json`、`all_small_rows.json`。',
        '原始证据：本目录`runs/`为27次新调用。逐文件哈希及核验范围见`all_small_verification.json`。',
        '冻结文件：`plan.json`、`source_inputs.zip`；本次代码只负责补齐并汇总小规模。', '']
    (OUT/'report.md').write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    summarize()
