# Historical M8 experiments: retain their original two-stage semantics.
"""Compare M3 with M8 v2 on all 22 unmodified small Zhou instances.

M3 is solved first; one returned baseline is shared by M8 exact, no-transfer
exact control, and all M8 ALNS seeds. Each result is persisted and re-audited.
A conditional M8 certificate is never relabelled as a joint routing optimum.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import resource
import shutil
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = 'output/experiments/model8_vs_model3_small_20260922'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def problem_data(args):
    from models.routing.common.data_loader import parse_dataset
    from models.routing.common.domain.instance import ProblemInstance
    from models.routing.common.task_builder import build_tasks_for_model
    source = ROOT / 'data/zhou_later_pickup/small' / args.scenario / f'{args.instance}.csv'
    fleet_path = ROOT / 'config/routing/validation/zhou_small.json'
    fleet = json.loads(fleet_path.read_text())
    nodes, _ = parse_dataset(str(source))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get('speed', 1))
    problem.aggregate_demands_from_pre_assignment()
    e1, e2 = build_tasks_for_model(problem, 8)
    return problem, e1, e2, source, fleet


def worker(args):
    from models.routing.variants.model_3.exact_solver import solve_exact as solve_m3
    from models.routing.variants.model_8.two_stage_v2.exact_solver import solve_exact as solve_m8
    from models.routing.variants.model_8.two_stage_v2.alns import solve_alns
    from models.routing.variants.model_8.two_stage_v2.baseline import prepare_baseline
    from models.routing.variants.model_8.two_stage_v2.state import Evaluator
    from models.routing.common.solution_audit import audit_exact_solution

    problem, e1, e2, source, fleet = problem_data(args)
    folder = ROOT / args.output / args.scenario / args.instance
    folder.mkdir(parents=True, exist_ok=True)
    count = problem.fleet.e2_per_sat_count
    write(folder / 'input.json', {'source': str(source.relative_to(ROOT)),
          'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'fleet': fleet,
          'customers': len(problem.customers), 'scenario': args.scenario,
          'budgets': {'m3': args.m3_time, 'm8_exact': args.exact_time,
                      'm8_control': args.exact_time, 'm8_alns_each': args.alns_time},
          'seeds': args.seeds, 'gurobi_threads': args.threads})
    t = time.perf_counter()
    a, b, info = solve_m3(problem, e1, e2, count, verbose=False,
                        time_limit=args.m3_time, return_info=True,
                        return_incumbent_on_time_limit=True,
                        gurobi_params={'Threads': args.threads, 'Seed': 42,
                                       'MIPGap': 0, 'MIPGapAbs': 0})
    record = {'e1_routes': a, 'e2_routes': b, 'info': info,
              'wall_seconds': time.perf_counter() - t}
    if a is None or b is None:
        write(folder / 'm3.json', record)
        raise RuntimeError('M3 did not return a feasible baseline')
    audit = audit_exact_solution(problem, 3, a, b, objective=info['objective'], tfp=9999.0)
    if not audit['valid']:
        raise RuntimeError(f'M3 independent audit failed: {audit}')
    base = prepare_baseline(problem, e1, e2, count, (a, b))
    record.update(independent_audit=audit, baseline_fingerprint=base['route_fingerprint'])
    write(folder / 'm3.json', record)
    shared = dict(problem=problem, e1_tasks=e1, e2_tasks_by_sat=e2,
                  vehicles_per_satellite=count, base_routes=(a, b), base_info=info,
                  verbose=False, return_info=True)
    evaluator = Evaluator(problem, e1, e2, base)
    jobs = [('m8_control', False, None), ('m8_exact', True, None)]
    jobs += [(f'm8_alns_{seed}', True, seed) for seed in args.seeds]
    for name, allow, seed in jobs:
        t = time.perf_counter()
        if seed is None:
            x, y, result = solve_m8(**shared, allow_transfers=allow,
                                    time_limit=args.exact_time,
                                    return_incumbent_on_time_limit=True,
                                    gurobi_params={'Threads': args.threads, 'Seed': 42})
        else:
            x, y, result = solve_alns(**shared, allow_transfers=True,
                                      max_time=args.alns_time, max_iterations=1000000,
                                      seed=seed, repair_time_limit=.5, max_remove=3)
        if x is None or y is None or not result.get('feasible'):
            raise RuntimeError(f'{name} returned no feasible result')
        if result['baseline']['route_fingerprint'] != base['route_fingerprint']:
            raise RuntimeError('Different baseline used between methods')
        checked = evaluator.audit(result['state'])
        if x != a or y != result['state']['e2_routes'] or not math.isclose(
                checked['objective'], result['objective'], rel_tol=1e-9, abs_tol=1e-6):
            raise RuntimeError('M8 independent reconstruction mismatch')
        if not allow and result['transshipment_count']:
            raise RuntimeError('Control contains a handoff')
        if result['objective'] > info['objective'] + 1e-5:
            raise RuntimeError('M8 lost the feasible baseline incumbent')
        record = {'e1_routes': x, 'e2_routes': y, 'info': result,
                  'seed': seed, 'wall_seconds': time.perf_counter() - t,
                  'reloaded_audit_valid': True,
                  'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                                  / (1024 ** 2 if sys.platform == 'darwin' else 1024)}
        write(folder / f'{name}.json', record)
        print(json.dumps({'instance': args.instance, 'method': name, 'objective': result['objective'],
                          'conditional_proven': result['conditional_proven_optimal'],
                          'transfers': result['transshipment_count']}, ensure_ascii=False), flush=True)


def read_json(path):
    return json.loads(path.read_text()) if path.exists() else None


def generate_report(args, instances, jobs):
    folder = ROOT / args.output / args.scenario
    rows = []
    audits = []
    for instance in instances:
        path = folder / instance
        meta, base = read_json(path / 'input.json'), read_json(path / 'm3.json')
        if not base or base['e1_routes'] is None:
            continue
        b = base['info']
        control = read_json(path / 'm8_control.json')
        exact = read_json(path / 'm8_exact.json')
        alns = [read_json(path / f'm8_alns_{s}.json') for s in args.seeds]
        alns = [a for a in alns if a]
        ci, ei = (control or {}).get('info', {}), (exact or {}).get('info', {})
        costs = [r['info']['objective'] for r in alns]
        best = min(alns, key=lambda r: r['info']['objective']) if alns else None
        best_exact = ei.get('objective')
        gap = None
        if costs and ei.get('conditional_proven_optimal'):
            gap = max(0.0, (min(costs) - best_exact) / max(abs(best_exact), 1e-9) * 100)
            if min(costs) < best_exact - 1e-5:
                raise RuntimeError('ALNS beats supposedly proven conditional optimum')
        row = {'instance': instance, 'customers': meta['customers'],
               'm3_objective': b['objective'], 'm3_proven': bool(b.get('proven_optimal')),
               'm3_lower_bound': b.get('best_bound'), 'm3_wall_seconds': base['wall_seconds'],
               'control_objective': ci.get('objective'), 'control_proven': bool(ci.get('conditional_proven_optimal')),
               'm8_exact_objective': best_exact, 'm8_exact_proven': bool(ei.get('conditional_proven_optimal')),
               'm8_exact_lower_bound': ei.get('conditional_best_bound'),
               'm8_exact_wall_seconds': (exact or {}).get('wall_seconds'),
               'm8_exact_transfers': ei.get('transshipment_count'),
               'exact_improvement': None if best_exact is None else b['objective'] - best_exact,
               'certified_relay_value': ci['objective'] - best_exact if ci.get('conditional_proven_optimal')
                                         and ei.get('conditional_proven_optimal') else None,
               'alns_best': min(costs) if costs else None,
               'alns_mean': statistics.mean(costs) if costs else None,
               'alns_worst': max(costs) if costs else None,
               'alns_improvement': b['objective'] - min(costs) if costs else None,
               'alns_gap_to_conditional_optimum_percent': gap,
               'alns_runs': len(alns),
               'alns_best_transfers': best['info']['transshipment_count'] if best else None,
               'alns_iterations': [r['info']['iterations'] for r in alns],
               'baseline_fingerprint': base['baseline_fingerprint']}
        rows.append(row)
        audits.append({'instance': instance, 'm3_valid': base['independent_audit']['valid'],
                       'm8_saved_valid': [r['reloaded_audit_valid'] for r in [control, exact] + alns if r]})
    complete = len(jobs) == len(instances) and all(j['status'] == 'completed' for j in jobs)
    stats = {'instances_planned': len(instances), 'instances_with_base': len(rows),
             'complete': complete, 'm3_proven_count': sum(r['m3_proven'] for r in rows),
             'control_proven_count': sum(r['control_proven'] for r in rows),
             'm8_exact_proven_count': sum(r['m8_exact_proven'] for r in rows),
             'exact_improved_count': sum((r['exact_improvement'] or 0) > 1e-5 for r in rows),
             'alns_improved_count': sum((r['alns_improvement'] or 0) > 1e-5 for r in rows),
             'alns_runs': sum(r['alns_runs'] for r in rows),
             'alns_best_matches_exact_count': sum(r['alns_gap_to_conditional_optimum_percent'] is not None
                                                 and r['alns_gap_to_conditional_optimum_percent'] <= 1e-6 for r in rows)}
    write(folder / 'summary.json', {'settings': vars(args), 'statistics': stats,
                                   'jobs': jobs, 'results': rows, 'audits': audits})
    lines = ['# 小规模实例：M3 与 M8 容量协作对比', '', '## Material Passport', '',
             f'- 状态：{"全部完成" if complete else "进行中，未完成行不得视为最终结论"}。',
             f'- 数据：`data/zhou_later_pickup/small/{args.scenario}`，全部 {len(instances)} 个原有小算例。',
             '- 参数：`config/routing/validation/zhou_small.json`；E2 体积容量 5、重量容量 300、每仓最多 4 车；数据和参数未修改。',
             f'- M3 精确预算 {args.m3_time}s；M8 条件精确/禁止交接对照各 {args.exact_time}s；M8 ALNS 每种子 {args.alns_time}s，种子 {args.seeds}。',
             '- M8 各分支共享同一个 M3 基础解；保留 E1 与配送骨架，回收可重分配、重排。',
             '- 禁止交接对照拥有同样的回收调整权限；两者条件最优值差才是该骨架下最优传递的增量收益。',
             '- 第二阶段耗时另计，不将额外优化预算伪装成与 M3 同预算的端到端算法比较。',
             '- M8 最优证明只针对当前配送骨架，不能推广为完整 M6 联合问题或其他 M3 并列最优解的结论。',
             '- 路线、求解上下界、载荷和时序检查保存在每例目录；源代码与配置快照位于上级 `source/`。', '',
             '## 完成概况', '',
             f'- M3 已证明最优：{stats["m3_proven_count"]}/{len(instances)}；M8 条件最优：{stats["m8_exact_proven_count"]}/{len(instances)}；禁止交接对照条件最优：{stats["control_proven_count"]}/{len(instances)}。',
             f'- M8 精确改善实例数：{stats["exact_improved_count"]}；ALNS 改善实例数：{stats["alns_improved_count"]}。',
             f'- ALNS 已完成 {stats["alns_runs"]} 次；每例最好值达到 M8 条件最优的实例数：{stats["alns_best_matches_exact_count"]}。', '',
             '| 实例 | 客户数 | M3 距离 | M3最优 | 禁交接距离 | M8精确距离 | M8条件最优 | M8 ALNS最好 | ALNS均值 | M8精确节省 |',
             '|---|---:|---:|---|---:|---:|---|---:|---:|---:|']
    def fmt(x):
        return '—' if x is None else f'{x:.6f}'
    for r in rows:
        lines.append(f"| {r['instance']} | {r['customers']} | {fmt(r['m3_objective'])} | {r['m3_proven']} | "
                     f"{fmt(r['control_objective'])} | {fmt(r['m8_exact_objective'])} | {r['m8_exact_proven']} | "
                     f"{fmt(r['alns_best'])} | {fmt(r['alns_mean'])} | {fmt(r['exact_improvement'])} |")
    lines += ['', '## 执行记录', '']
    lines += [f"- {j['instance']}: {j['status']}，{j['seconds']:.2f}s。" for j in jobs]
    (folder / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=['standard_same_day', 'short_same_day', 'next_day'], default='standard_same_day')
    parser.add_argument('--instances', nargs='*')
    parser.add_argument('--m3-time', type=float, default=30)
    parser.add_argument('--exact-time', type=float, default=30)
    parser.add_argument('--alns-time', type=float, default=2)
    parser.add_argument('--seeds', nargs='+', type=int, default=[42, 7, 19])
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--hard-timeout', type=float, default=150)
    parser.add_argument('--output', default=DEFAULT_OUTPUT)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--instance')
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    instances = args.instances or [p.stem for p in sorted(
        (ROOT / 'data/zhou_later_pickup/small' / args.scenario).glob('*.csv'),
        key=lambda p: (int(p.stem.split('_')[1].split('-')[0]), p.stem))]
    if not instances:
        raise ValueError('No small instances')
    output = ROOT / args.output
    manifest = {'settings': vars(args), 'instances': instances, 'python': sys.version,
                'source_sha256': {}}
    source_files = [Path(__file__).relative_to(ROOT), Path('config/routing/validation/zhou_small.json')]
    source_files += list(Path('models/routing/variants/model_8').glob('*.py'))
    source_files += [Path(p) for p in ['models/routing/variants/model_8/two_stage_v2/alns.py',
                     'models/routing/variants/model_6/model.py', 'models/routing/variants/model_5/model.py',
                     'models/routing/variants/model_3/exact_solver.py',
                     'models/routing/common/commodity_flow.py', 'models/routing/common/solution_audit.py']]
    for source in source_files:
        target = output / 'source' / source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / source, target)
        manifest['source_sha256'][str(source)] = hashlib.sha256((ROOT / source).read_bytes()).hexdigest()
    write(output / args.scenario / 'manifest.json', manifest)
    jobs = []
    for instance in instances:
        folder = output / args.scenario / instance
        folder.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, '-m', 'experiments.runs.run_model8_small_comparison',
               '--worker', '--instance', instance, '--scenario', args.scenario,
               '--m3-time', str(args.m3_time), '--exact-time', str(args.exact_time),
               '--alns-time', str(args.alns_time), '--threads', str(args.threads),
               '--output', args.output, '--seeds', *map(str, args.seeds)]
        started = time.perf_counter()
        with (folder / 'worker.log').open('w') as log:
            try:
                completed = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                           timeout=args.hard_timeout, check=False)
                status = 'completed' if completed.returncode == 0 else f'failed:{completed.returncode}'
            except subprocess.TimeoutExpired:
                status = 'hard_timeout'
        job = {'instance': instance, 'status': status, 'seconds': time.perf_counter() - started}
        jobs.append(job)
        stats = generate_report(args, instances, jobs)
        print(json.dumps({**job, 'm3_proven': stats['m3_proven_count'],
                          'm8_proven': stats['m8_exact_proven_count'],
                          'm8_improved': stats['exact_improved_count']}, ensure_ascii=False), flush=True)
        if status != 'completed':
            raise RuntimeError(f'Inspect worker failure: {folder / "worker.log"}')
    print(output / args.scenario / 'report.md', flush=True)


if __name__ == '__main__':
    main()
