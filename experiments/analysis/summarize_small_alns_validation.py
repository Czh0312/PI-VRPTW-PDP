"""Reaudit saved ALNS routes and report exact gaps, seeds and paired gains."""
import csv
import json
import statistics as stats

from experiments.runs.run_small_alns_validation import OUT, ROOT, MODELS, read_rows, report
from experiments.runs.run_model12346_alns_scaling import load_problem
from models.routing.common.solution_audit import audit_exact_solution


def main():
    manifest = json.loads((OUT / 'manifest.json').read_text(encoding='utf-8'))
    rows = read_rows(OUT / 'runs.jsonl')
    audits = []
    for row in rows:
        if row['model'] == 1 and row.get('info', {}).get('feasible'):
            problem = load_problem(ROOT / 'data/zhou_later_pickup/small/standard_same_day' / (row['instance'] + '.csv'), manifest['fleet'])
            # M1 exact E1 routes use physical satellite ids; ALNS uses Sd tasks.
            satellite_tasks = {s.id + 'd': s.id for s in problem.satellites}
            normalized = [[satellite_tasks.get(node, node) for node in route] for route in row['e1_routes']]
            row['audit'] = audit_exact_solution(problem, 1, normalized, row['e2_routes'], objective=row['objective'])
            row['valid'] = row['audit']['valid'] and abs(row['route_cost_delta']) < 1e-6
            row['gap_pct'] = 100*(row['objective']-row['exact_objective'])/row['exact_objective'] if row['valid'] else None
        audits.append(dict(instance=row['instance'], model=row['model'], seed=row['seed'], valid=row['valid'], audit=row.get('audit'), error=row.get('error')))
    (OUT / 'independent_audits.json').write_text(json.dumps(audits, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / 'verified_runs.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows), encoding='utf-8')
    report(rows, 396)
    fields = ['instance', 'customers', 'model', 'seed', 'valid', 'objective', 'exact_objective', 'gap_pct', 'wall_time']
    with (OUT / 'paired_results.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    extra = ['', '## 按规模与种子统计', '', '| 模型 | 客户数 | 配对次数 | 命中 | 平均偏差% | 最大偏差% |', '| --- | ---: | ---: | ---: | ---: | ---: |']
    for model in MODELS:
        for size in (7, 10, 12):
            subset = [r for r in rows if r['model'] == model and r['customers'] == size and r['valid'] and r['gap_pct'] is not None]
            if subset:
                extra.append(f"| M{model} | {size} | {len(subset)} | {sum(abs(r['objective']-r['exact_objective'])<=1e-5 for r in subset)} | {stats.mean(r['gap_pct'] for r in subset):.4f} | {max(r['gap_pct'] for r in subset):.4f} |")
    extra += ['', '| 模型 | 种子 | 精确配对 | 命中 | 平均偏差% |', '| --- | ---: | ---: | ---: | ---: |']
    for model in MODELS:
        for seed in (42, 43, 44):
            subset = [r for r in rows if r['model'] == model and r['seed'] == seed and r['valid'] and r['gap_pct'] is not None]
            if subset:
                extra.append(f"| M{model} | {seed} | {len(subset)} | {sum(abs(r['objective']-r['exact_objective'])<=1e-5 for r in subset)} | {stats.mean(r['gap_pct'] for r in subset):.4f} |")
    extra += ['', '## 全部实例的同种子配对', '', '改善率为 (前者−后者)/前者；正数表示后者更好。', '', '| 配对 | 次数 | 平均改善% | 后者胜/平/负 |', '| --- | ---: | ---: | ---: |']
    for left, right in ((1,2), (3,4), (3,6), (4,7), (6,7)):
        a = {(r['instance'],r['seed']):r for r in rows if r['model']==left and r['valid']}
        b = {(r['instance'],r['seed']):r for r in rows if r['model']==right and r['valid']}
        gains = [100*(a[k]['objective']-b[k]['objective'])/a[k]['objective'] for k in a.keys() & b.keys()]
        if gains:
            extra.append(f"| M{left} → M{right} | {len(gains)} | {stats.mean(gains):.4f} | {sum(g>1e-6 for g in gains)}/{sum(abs(g)<=1e-6 for g in gains)}/{sum(g< -1e-6 for g in gains)} |")
    bad = [r for r in rows if not r['valid']]
    extra += ['', '## 审计说明', '', 'M1 原始 E1 路线使用 Sd，审计副本将其映射为同一卫星物理节点 S；不改变路线顺序或成本。原始运行记录保留在 runs.jsonl，规范化审计结果在 verified_runs.jsonl。',
              f'校验未通过的运行数：{len(bad)}。', f"最大距离复算误差：{max((abs(r['route_cost_delta']) for r in rows if r.get('route_cost_delta') is not None), default=0):.3g}。"]
    for r in bad:
        extra.append(f"- {r['instance']} M{r['model']} seed={r['seed']}: {r.get('error') or r.get('audit')}")
    with (OUT / 'report.md').open('a', encoding='utf-8') as handle:
        handle.write('\n'.join(extra)+'\n')
    print(f'Validated {len(rows)} runs; failures: {len(bad)}')


if __name__ == '__main__':
    main()
