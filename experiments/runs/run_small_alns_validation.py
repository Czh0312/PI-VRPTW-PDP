"""Independent, resumable six-model small ALNS benchmark and exact comparison."""
import hashlib
import json
import platform
import statistics
import sys
import time
from pathlib import Path

from experiments.runs.run_model12346_alns_scaling import ROOT, load_problem, route_cost, solve_one
from models.routing.common.solution_audit import audit_exact_solution

OUT = ROOT / 'output/experiments/small_alns_validation_20260908'
MODELS = (1, 2, 3, 4, 6, 7)
SEEDS = (42, 43, 44)
SECONDS = 5.0


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.exists() else []


def references():
    paths = [ROOT / 'output/results/final_freeze_20260830/exact/small_exact_20260830_220305.jsonl',
             ROOT / 'output/results/m7_optimization_20260901/proof_auto_strategy_40_20260901_202801.jsonl']
    refs = {}
    for path in paths:
        for r in read_rows(path):
            if r.get('proven_optimal'):
                refs[(r.get('model', 7), r['instance'])] = r['objective']
    return refs, paths


def report(rows, expected):
    summaries = []
    for model in MODELS:
        rs = [r for r in rows if r['model'] == model]
        valid = [r for r in rs if r['valid']]
        paired = [r for r in valid if r['exact_objective'] is not None]
        gaps = [r['gap_pct'] for r in paired]
        summaries.append(dict(model=model, runs=len(rs), valid=len(valid), exact_pairs=len(paired),
            hits=sum(abs(r['objective'] - r['exact_objective']) <= 1e-5 for r in paired),
            mean_gap_pct=statistics.mean(gaps) if gaps else None,
            max_gap_pct=max(gaps) if gaps else None,
            mean_seconds=statistics.mean(r['wall_time'] for r in rs) if rs else None,
            relay_runs=sum(bool(r.get('info', {}).get('transshipment_count')) for r in valid)))
    result = dict(completed=len(rows), expected=expected, models=summaries)
    (OUT / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 六模型小规模 ALNS 统一复跑', '', f'完成 {len(rows)}/{expected} 次。22 个标准当日实例；seed=42/43/44；每次上限 5 秒，迭代上限 1,000,000。',
        '各模型独立从头求解，串行执行；不传入精确目标或其他模型的解。M6/M7 阶段一比例为 0.8/0.75。',
        '最优值偏差只使用有严格证明的实例；M7 为 13 个实例，其他模型各 22 个。', '',
        '| 模型 | 校验可行/运行 | 精确配对 | 命中 | 平均偏差% | 最大偏差% | 平均秒 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for s in summaries:
        def fmt(key):
            return '—' if s[key] is None else f'{s[key]:.4f}'
        lines.append(f"| M{s['model']} | {s['valid']}/{s['runs']} | {s['exact_pairs']} | {s['hits']} | {fmt('mean_gap_pct')} | {fmt('max_gap_pct')} | {fmt('mean_seconds')} |")
    lines += ['', '无交接解进行独立任务覆盖、容量、时间窗与同步审计；含交接解保留模型评估结果并独立复算路线距离。',
              'M7 未证明最优实例的 exact_objective/gap_pct 留空。命中最优值不代表 ALNS 自行证明最优。',
              '完整路线、校验详情和逐次结果见 runs.jsonl；配置、源码与输入哈希见 manifest.json。']
    (OUT / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    paths = sorted((ROOT / 'data/zhou_later_pickup/small/standard_same_day').glob('*.csv'))
    assert len(paths) == 22
    fleet_path = ROOT / 'config/routing/validation/zhou_small.json'
    fleet = json.loads(fleet_path.read_text(encoding='utf-8'))
    refs, ref_paths = references()
    sources = sorted((ROOT / 'models/routing').rglob('*.py')) + paths + [fleet_path, Path(__file__)] + ref_paths
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    manifest_path = OUT / 'manifest.json'
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text(encoding='utf-8'))
        assert old['sha256'] == hashes, 'Inputs or source changed; use a new experiment directory'
    else:
        manifest_path.write_text(json.dumps(dict(models=MODELS, seeds=SEEDS, seconds=SECONDS,
            instances=[p.stem for p in paths], python=sys.executable, platform=platform.platform(),
            fleet=fleet, sha256=hashes, protocol='independent_serial', target=None), indent=2), encoding='utf-8')
    rows = read_rows(OUT / 'runs.jsonl')
    done = {(r['instance'], r['seed'], r['model']) for r in rows}
    assert len(done) == len(rows)
    expected = len(paths) * len(SEEDS) * len(MODELS)
    with (OUT / 'runs.jsonl').open('a', encoding='utf-8') as handle:
        for path in paths:
            for seed in SEEDS:
                for model in MODELS:
                    if (path.stem, seed, model) in done:
                        continue
                    problem = load_problem(path, fleet)
                    start = time.perf_counter()
                    row = dict(instance=path.stem, customers=len(problem.customers), seed=seed,
                               model=model, time_budget=SECONDS, exact_objective=refs.get((model, path.stem)))
                    try:
                        r1, r2, assignment, info = solve_one(model, problem, max_time=SECONDS,
                            iterations=1_000_000, seed=seed)
                        elapsed = time.perf_counter() - start
                        objective = info.get('objective')
                        cost = route_cost(problem, r1, r2)
                        delta = cost - objective if cost is not None and objective is not None else None
                        audit = None
                        if info.get('feasible') and not info.get('transshipment_count', 0):
                            audit = audit_exact_solution(problem, {6: 3, 7: 4}.get(model, model),
                                r1, r2, assignment=assignment, objective=objective)
                        valid = bool(info.get('feasible') and delta is not None and abs(delta) < 1e-6
                                     and (audit is None or audit['valid']))
                        ref = row['exact_objective']
                        row.update(objective=objective, valid=valid, wall_time=elapsed, info=info,
                            route_cost_delta=delta, audit=audit, e1_routes=r1, e2_routes=r2, assignment=assignment,
                            gap_pct=100*(objective-ref)/ref if valid and ref is not None else None)
                    except Exception as exc:
                        row.update(objective=None, valid=False, wall_time=time.perf_counter()-start,
                                   gap_pct=None, error=repr(exc))
                    handle.write(json.dumps(row, ensure_ascii=False) + '\n')
                    handle.flush()
                    rows.append(row)
                    report(rows, expected)
                    print(f"{len(rows)}/{expected} {path.stem} seed={seed} M{model} valid={row['valid']} gap={row['gap_pct']}", flush=True)
    assert all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in hashes.items())
    assert len(rows) == expected
    print('COMPLETE', flush=True)


if __name__ == '__main__':
    main()
