# Historical M8 experiments: retain their original two-stage semantics.
"""Re-read and independently audit the small M3/M8 experiment artifacts."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
from copy import deepcopy
import shutil

from models.routing.common.data_loader import parse_dataset
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.task_builder import build_tasks_for_model
from models.routing.common.solution_audit import audit_exact_solution
from models.routing.variants.model_8.two_stage_v2.baseline import prepare_baseline
from models.routing.variants.model_8.two_stage_v2.state import Evaluator

ROOT = Path(__file__).resolve().parents[2]


def close(a, b):
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default='output/experiments/model8_vs_model3_small_20260922/standard_same_day')
    args = parser.parse_args()
    folder = ROOT / args.input
    summary = json.loads((folder / 'summary.json').read_text())
    if not summary['statistics']['complete']:
        raise RuntimeError('Experiment is incomplete; do not issue a final summary')
    initial_statistics = deepcopy(summary['statistics'])
    refinements = []
    for row in summary['results']:
        path = folder / row['instance'] / 'm8_exact_refined.json'
        if not path.exists():
            row['selected_exact_result'] = 'm8_exact.json'
            continue
        result = json.loads(path.read_text())
        i = result['info']
        assert i['baseline']['route_fingerprint'] == row['baseline_fingerprint']
        assert i['objective'] <= row['m8_exact_objective'] + 1e-6
        if result['refinement_method'] == 'valid_lower_bound_certificate':
            cert = i['certificate']
            assert cert['metric_verified'] and cert['closed']
            assert close(cert['lower_bound'], i['objective'])
        row.update(m8_exact_objective=i['objective'],
                   m8_exact_proven=i['conditional_proven_optimal'],
                   m8_exact_lower_bound=i['conditional_best_bound'],
                   m8_exact_transfers=i['transshipment_count'],
                   exact_improvement=row['m3_objective'] - i['objective'],
                   selected_exact_result=path.name,
                   refinement_method=result['refinement_method'],
                   refinement_wall_seconds=result['wall_seconds'])
        if i['conditional_proven_optimal']:
            row['certified_relay_value'] = row['control_objective'] - i['objective']
            row['alns_gap_to_conditional_optimum_percent'] = max(
                0.0, (row['alns_best'] - i['objective']) / max(abs(i['objective']), 1e-9) * 100)
            assert row['alns_best'] >= i['objective'] - 1e-6
        refinements.append({'instance': row['instance'], 'method': result['refinement_method'],
                            'seconds': result['wall_seconds'],
                            'conditional_proven': i['conditional_proven_optimal']})
    stats = summary['statistics']
    stats['m8_exact_proven_count'] = sum(r['m8_exact_proven'] for r in summary['results'])
    stats['exact_improved_count'] = sum(r['exact_improvement'] > 1e-5 for r in summary['results'])
    stats['alns_best_matches_exact_count'] = sum(
        r['alns_gap_to_conditional_optimum_percent'] is not None
        and r['alns_gap_to_conditional_optimum_percent'] <= 1e-6 for r in summary['results'])
    summary['initial_statistics'] = initial_statistics
    summary['refinements'] = refinements
    (folder / 'final_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    checks = []
    for row in summary['results']:
        path = folder / row['instance']
        meta = json.loads((path / 'input.json').read_text())
        data = ROOT / meta['source']
        assert hashlib.sha256(data.read_bytes()).hexdigest() == meta['source_sha256']
        nodes, _ = parse_dataset(str(data))
        p = ProblemInstance(nodes, meta['fleet'], speed=meta['fleet'].get('speed', 1))
        p.aggregate_demands_from_pre_assignment()
        e1, e2 = build_tasks_for_model(p, 8)
        base = json.loads((path / 'm3.json').read_text())
        a, b, info = base['e1_routes'], base['e2_routes'], base['info']
        audit = audit_exact_solution(p, 3, a, b, objective=info['objective'], tfp=9999.0)
        assert audit['valid'], audit
        snapshot = prepare_baseline(p, e1, e2, p.fleet.e2_per_sat_count, (a, b))
        assert snapshot['route_fingerprint'] == row['baseline_fingerprint']
        evaluator = Evaluator(p, e1, e2, snapshot)
        checks.append({'instance': row['instance'], 'method': 'm3', 'valid': True})
        for saved in sorted(path.glob('m8*.json')):
            result = json.loads(saved.read_text())
            i = result['info']
            assert i['baseline']['route_fingerprint'] == snapshot['route_fingerprint']
            assert result['e1_routes'] == a
            assert result['e2_routes'] == i['state']['e2_routes']
            checked = evaluator.audit(i['state'])
            assert close(checked['objective'], i['objective'])
            if saved.stem == 'm8_control':
                assert not i['state']['transfers']
            checks.append({'instance': row['instance'], 'method': saved.stem, 'valid': True,
                           'objective': checked['objective'], 'transfers': i['transshipment_count']})
    manifest = json.loads((folder / 'manifest.json').read_text())
    source_checks = {}
    for rel, expected in manifest['source_sha256'].items():
        saved = folder.parent / 'source' / rel
        source_checks[rel] = hashlib.sha256(saved.read_bytes()).hexdigest() == expected
    assert all(source_checks.values())
    verification = {'status': 'PASS', 'solutions_checked': len(checks),
                    'checks': checks, 'source_snapshot_matches_manifest': source_checks}
    (folder / 'verification.json').write_text(json.dumps(verification, ensure_ascii=False, indent=2))
    rows, stats = summary['results'], summary['statistics']
    def f(x):
        return '—' if x is None else f'{0.0 if abs(x) < 5e-7 else x:.6f}'
    all_closed = all(r['m3_proven'] and r['m8_exact_proven'] and r['control_proven'] for r in rows)
    all_equal = all(close(r['m3_objective'], r['m8_exact_objective']) for r in rows)
    lines = ['# 小算例 M3 与 M8：结果解读', '', '## Material Passport', '',
             '- 状态：实际实验完成；全部保存的解已重新加载检查。',
             f'- 情景：{summary["settings"]["scenario"]}；{len(rows)} 个现有 7/10/12 客户算例；原始容量、时间窗、分仓参数不变。',
             '- 首轮逐例表：[report.md](report.md)；含补充证书的最终结果：[final_summary.json](final_summary.json)。',
             '- M3 精确解是每例共同基础方案，M8 固定 E1 与配送骨架，联合调整回收与交接；禁止交接对照保留相同回收自由度。',
             '- M8 精确证书为给定配送骨架下的条件最优；ALNS 是额外第二阶段预算。', '',
             '## 结果', '']
    if all_closed and all_equal:
        lines.append(f'全部 {len(rows)} 例的 M3 已证明全局最优，M8 已证明条件最优，二者距离相同。'
                     '禁止交接的条件最优值也相同。因此在此次选定的配送骨架下，开放空箱传递没有降低总距离。')
    else:
        lines.append(f'M3 最优证明 {stats["m3_proven_count"]}/{len(rows)}，M8 条件最优证明 '
                     f'{stats["m8_exact_proven_count"]}/{len(rows)}。M8 精确改善 {stats["exact_improved_count"]} 例，'
                     f'ALNS 改善 {stats["alns_improved_count"]} 例；未闭合的行不能当作无收益证明。')
    lines += ['', '| 客户数 | 实例数 | M3平均距离 | M8精确平均距离 | M8 ALNS最好值平均 | 精确改善例数 |',
              '|---:|---:|---:|---:|---:|---:|']
    for count in sorted({r['customers'] for r in rows}):
        group = [r for r in rows if r['customers'] == count]
        values = lambda key: statistics.mean(r[key] for r in group)
        lines.append(f"| {count} | {len(group)} | {values('m3_objective'):.6f} | "
                     f"{values('m8_exact_objective'):.6f} | {values('alns_best'):.6f} | "
                     f"{sum((r['exact_improvement'] or 0) > 1e-5 for r in group)} |")
    lines += ['', f'ALNS 使用种子 {summary["settings"]["seeds"]}，每例每种子 '
              f'{summary["settings"]["alns_time"]} 秒，共 {stats["alns_runs"]} 次。'
              f'每例最好值达到条件精确最优的实例数为 {stats["alns_best_matches_exact_count"]}。'
              '因为 ALNS 始终保留基础可行解，在零收益实例中达到最优并不代表它发现了新的传递结构。', '',
              '## 首轮与补充证明', '',
              f'- 首轮完整条件 MILP 证明 {initial_statistics["m8_exact_proven_count"]}/{len(rows)} 例，原始 30 秒结果未覆盖或改写。',
              '- 未闭合实例使用现有的度量路径捷径/零回收载荷松弛：保留所有配送和回收访问及时窗，'
              '去掉 E2 空箱载荷约束并开放 M3 路径。核验距离与时间的三角不等式后，所得严格下界适用于 M8。',
              '- 下界等于已审计的可行上界时可以闭合最优性证明，不需要声称原 MILP 返回 OPTIMAL。',
              *[f"- {r['instance']}：{r['method']}，补充耗时 {r['seconds']:.3f}s，条件证明 {r['conditional_proven']}。" for r in refinements], '',
              '## 求解与审计', '',
              f'- 重新审计 {len(checks)} 个解：任务覆盖、配送骨架、逐段重量/体积、原生/已接收空箱、单次交接、时间窗及两级衔接全部通过。',
              '- 输入数据哈希与源代码快照哈希核验通过，见 [verification.json](verification.json)。',
              f'- M3 实际耗时合计 {sum(r["m3_wall_seconds"] for r in rows):.3f}s；'
              f'M8 条件精确实际耗时合计 {sum(r["m8_exact_wall_seconds"] for r in rows):.3f}s。', '',
              '## 结论范围', '',
              '这些结果覆盖原有标准当日回收小算例，不能推广到不同容量、回收时间窗或车辆数，'
              '也不能排除更换配送骨架或选择另一个 M3 并列最优解后存在收益。'
              '之前构造例中的 10.8% 改善属于机制验证，不与本批原始算例混合统计。']
    lines += ['', '## 最终逐例对比', '',
              '| 实例 | M3 | 禁止交接 | M8精确/证书 | M8 ALNS最好 | 节省 | M8证明方式 |',
              '|---|---:|---:|---:|---:|---:|---|']
    for row in rows:
        proof = row.get('refinement_method', 'conditional_milp')
        lines.append(f"| {row['instance']} | {f(row['m3_objective'])} | {f(row['control_objective'])} | "
                     f"{f(row['m8_exact_objective'])} | {f(row['alns_best'])} | {f(row['exact_improvement'])} | {proof} |")
    (folder / 'findings.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    additional_sources = {}
    for name in ('experiments/runs/refine_model8_small_exact.py',
                 'experiments/analysis/summarize_model8_small.py'):
        source = ROOT / name
        target = folder.parent / 'source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        additional_sources[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    (folder / 'refinement_source_sha256.json').write_text(json.dumps(additional_sources, indent=2))
    print(json.dumps({'solutions_checked': len(checks), 'all_exact_closed': all_closed,
                      'all_exact_equal': all_equal, 'statistics': stats}, ensure_ascii=False))


if __name__ == '__main__':
    main()
