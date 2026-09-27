# Historical M8 experiments: retain their original two-stage semantics.
"""Bounded large-instance M8 probe, preserving exact/heuristic proof scopes.

Each worker has an independent hard wall timeout. Results and construction
telemetry are persisted between stages, so a timeout never looks like zero gain.
"""
import argparse
import json
import math
from pathlib import Path
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def write(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def worker(args):
    from models.routing.common.data_loader import parse_dataset
    from models.routing.common.domain.instance import ProblemInstance
    from models.routing.common.task_builder import build_tasks_for_model
    from models.routing.variants.model_8.legacy.baseline import prepare_baseline

    folder = Path(args.output) / args.instance
    folder.mkdir(parents=True, exist_ok=True)
    nodes, _ = parse_dataset(str(ROOT / 'data/zhou_later_pickup/large/standard_same_day'
                                 / f'{args.instance}.csv'))
    fleet = json.loads((ROOT / 'config/routing/validation/zhou_large.json').read_text())
    problem = ProblemInstance(nodes, fleet, speed=fleet['speed'])
    problem.aggregate_demands_from_pre_assignment()
    e1, e2 = build_tasks_for_model(problem, 8)
    started = time.perf_counter()
    if args.worker == 'base_alns':
        from models.routing.alns.variants.model_3 import solve_alns
        a, b, info = solve_alns(problem, e1, e2, fleet['vehicles_per_satellite'],
                               max_time=args.base_time, max_iterations=1000000,
                               seed=args.seed, verbose=False, return_info=True)
        if a is not None:
            audit = prepare_baseline(problem, e1, e2, fleet['vehicles_per_satellite'], (a, b))
            info['independent_baseline_check'] = audit['route_fingerprint']
    elif args.worker == 'base_exact':
        from models.routing.variants.model_3.exact_solver import solve_exact
        warm = json.loads((folder / 'base_alns.json').read_text())
        a, b, info = solve_exact(
            problem, e1, e2, fleet['vehicles_per_satellite'],
            time_limit=args.exact_time, verbose=False, return_info=True,
            return_incumbent_on_time_limit=True,
            warm_start_routes=(warm['e1_routes'], warm['e2_routes']),
            gurobi_params={'Seed': args.seed, 'Threads': 4, 'SoftMemLimit': 1.5,
                           'MIPGap': 0, 'MIPGapAbs': 0},
        )
        if a is not None:
            audit = prepare_baseline(problem, e1, e2, fleet['vehicles_per_satellite'], (a, b))
            info['independent_baseline_check'] = audit['route_fingerprint']
    else:
        from models.routing.variants.model_8.legacy.alns import solve_alns
        from models.routing.variants.model_8.legacy import exact_solver
        candidates = []
        for name in ('base_alns', 'base_exact'):
            path = folder / f'{name}.json'
            if path.exists():
                candidate = json.loads(path.read_text())
                if candidate.get('e1_routes') is not None and candidate['info'].get('objective') is not None:
                    candidates.append(candidate)
        if not candidates:
            raise RuntimeError('No feasible M3 baseline available')
        base = min(candidates, key=lambda r: r['info']['objective'])
        certified = [r for r in candidates if r['info'].get('proven_optimal')
                     and r['info']['objective'] <= base['info']['objective'] + 1e-6]
        if certified:
            base = certified[0]
        # M8 prohibits new vehicles. Removing unused labels before construction
        # is equivalent to constructing their variables and fixing them to zero.
        count = max(len(routes) for routes in base['e2_routes'].values())
        base_info = base['info']
        proven = bool(base_info.get('proven_optimal'))
        write(folder / 'selected_base.json', {**base, 'effective_vehicle_count': count,
              'strict_m8_eligible': proven,
              'scope': 'optimal_base' if proven else 'best_found_base_exploratory'})
        if args.worker == 'relay_bound':
            from models.routing.variants.model_5.exact_solver import (
                _has_metric_distances, _zero_pickup_load_relaxation,
            )
            from models.routing.variants.model_3.exact_solver import solve_exact
            metric = _has_metric_distances(problem, e1, e2)
            if not metric:
                raise ValueError('Metric shortcut bound is invalid for these matrices')
            _a, _b, relaxation = solve_exact(
                problem, e1, _zero_pickup_load_relaxation(e2),
                fleet['vehicles_per_satellite'], time_limit=args.exact_time,
                verbose=False, return_info=True, return_incumbent_on_time_limit=True,
                warm_start_routes=(base['e1_routes'], base['e2_routes']),
                gurobi_params={'Seed': args.seed, 'Threads': 4, 'SoftMemLimit': 1.5,
                               'MIPGap': 0, 'MIPGapAbs': 0},
            )
            lower = relaxation.get('best_bound')
            upper = base_info['objective']
            valid = lower is not None and math.isfinite(lower) and lower < 1e90
            closed = valid and abs(upper - lower) <= max(1e-6, abs(upper) * 1e-9)
            result = {'instance': args.instance, 'worker': args.worker,
                      'wall_seconds': time.perf_counter() - started,
                      'metric_verified': metric, 'relaxation': relaxation,
                      'baseline_objective': upper, 'conditional_lower_bound': lower,
                      'max_possible_saving_bound': max(0.0, upper - lower) if valid else None,
                      'no_improving_relay_proven': closed,
                      'proof_scope': 'metric_zero_pickup_load_lower_bound_for_full_relay_model'}
            write(folder / 'relay_bound.json', result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return
        telemetry = []
        original = exact_solver.build_model

        def measured_build(*pos, **kw):
            event = {'index': len(telemetry) + 1, 'status': 'building'}
            telemetry.append(event)
            write(folder / 'construction.json', telemetry)
            t = time.perf_counter()
            result = original(*pos, **kw)
            model = result[0]
            event.update(status='built', seconds=time.perf_counter() - t,
                         variables=model.NumVars, constraints=model.NumConstrs,
                         peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                         / (1024 ** 2 if sys.platform == 'darwin' else 1024))
            write(folder / 'construction.json', telemetry)
            return result

        exact_solver.build_model = measured_build
        a, b, info = solve_alns(
            problem, e1, e2, count, base_routes=(base['e1_routes'], base['e2_routes']),
            base_info=base_info, stage1_method='exact' if proven else 'alns',
            max_time=args.stage2_time, max_iterations=args.max_iterations, max_remove=1,
            repair_time_limit=args.repair_time, seed=args.seed, verbose=False, return_info=True,
            gurobi_params={'Threads': 4, 'SoftMemLimit': 1.5},
        )
        info['probe_scope'] = 'optimal_base' if proven else 'best_found_base_exploratory'
        info['effective_vehicle_count'] = count
        info['construction'] = telemetry
    result = {'instance': args.instance, 'worker': args.worker,
              'e1_routes': a, 'e2_routes': b, 'info': info,
              'wall_seconds': time.perf_counter() - started,
              'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
              / (1024 ** 2 if sys.platform == 'darwin' else 1024)}
    write(folder / f'{args.worker}.json', result)
    print(json.dumps({k: info.get(k) for k in ('objective', 'status', 'proven_optimal',
                                             'iterations', 'transshipment_count')}, ensure_ascii=False), flush=True)


def report(args, jobs):
    out = Path(args.output)
    rows = []
    for instance in args.instances:
        folder = out / instance
        values = {}
        for stage in ('base_alns', 'base_exact', 'relay', 'relay_bound'):
            path = folder / f'{stage}.json'
            values[stage] = json.loads(path.read_text()) if path.exists() else None
        rows.append({'instance': instance, **values})
    write(out / 'summary.json', {'settings': vars(args), 'jobs': jobs, 'results': rows})
    lines = ['# M8 大规模试跑', '', '## Material Passport', '',
             '- 状态：实际运行；仅两个标准当日回收代表实例、一个随机种子。',
             f'- 种子：{args.seed}；M3 ALNS {args.base_time}s；M3 MIP {args.exact_time}s；传递阶段 {args.stage2_time}s。',
             f'- 每个子进程硬超时：{args.hard_timeout}s；计时包含建模，MIP 参数时间限制不包含建模。',
             f'- 传递搜索为 ALNS/MIP，最多释放一个回收任务，短/深修复分别{args.repair_time}/{3 * args.repair_time}秒，Threads=4。',
             '- 若 M3 未证明最优，严格 M8 无法认证；仍单独运行最好已知基础解上的探索性传递测试。',
             '- 第一阶段与第二阶段原始路线、参数、状态和建模统计均保存在同目录。', '',
             '| 实例 | M3 ALNS | M3 MIP上界 | M3下界 | M3最优已证明 | 传递后 | 节省 | 接力数 | 尝试数 |',
             '|---|---:|---:|---:|---|---:|---:|---:|---:|']
    def fmt(value):
        return '—' if value is None else f'{value:.3f}'
    details = []
    for row in rows:
        ai = (row['base_alns'] or {}).get('info', {})
        ei = (row['base_exact'] or {}).get('info', {})
        ri = (row['relay'] or {}).get('info', {})
        lines.append(f"| {row['instance']} | {fmt(ai.get('objective'))} | {fmt(ei.get('objective'))} | "
                     f"{fmt(ei.get('best_bound'))} | {bool(ei.get('proven_optimal'))} | "
                     f"{fmt(ri.get('objective'))} | {fmt(ri.get('transshipment_improvement'))} | "
                     f"{ri.get('transshipment_count', '—')} | {ri.get('iterations', '—')} |")
        if ri:
            built = ri.get('construction', [])
            solved = sum(h.get('candidate_objective') is not None for h in ri.get('search_history', []))
            details += ['', f"### {row['instance']}", '',
                      f"- 第二阶段耗时 {row['relay']['wall_seconds']:.2f}s；峰值 RSS {row['relay']['peak_rss_mib']:.1f} MiB。",
                      f"- 建模次数 {len(built)}；返回可行候选的修复次数 {solved}。",
                      f"- 建模耗时合计 {sum(x.get('seconds', 0) for x in built):.2f}s。",
                      f"- 严格基础解资格：{ri.get('stage1_proven_optimal')}；条件最优证明：{ri.get('conditional_proven_optimal')}。"]
        bound_path = out / row['instance'] / 'relay_bound.json'
        if bound_path.exists():
            bound = json.loads(bound_path.read_text())
            details += [f"- 额外严格松弛下界：{fmt(bound['conditional_lower_bound'])}；"
                        f"最多可能节省的上界：{fmt(bound['max_possible_saving_bound'])}；"
                        f"无正收益传递已证明：{bound['no_improving_relay_proven']}。"]
    lines += details
    lines += ['', '## 运行状态', ''] + [f"- {j['instance']} / {j['stage']}: {j['status']}，{j['wall_seconds']:.2f}s。" for j in jobs]
    lines += ['', '零节省仅表示本次预算内没有找到正收益；超时或没有完成修复不构成“无传递机会”的证据。',
              'M3 上下界未闭合时，不能将探索性测试写成最优基础解上的最优传递。', '']
    lines += ['额外松弛保留全部原始配送/回收访问和时间窗，仅去掉 E2 回收空箱载荷。',
              '验证距离和行驶时间满足三角不等式后，该问题的有效下界也是 M8 条件问题的下界。',
              '“最多可能节省”由基础解减下界得到，并不表示实际存在这样大的收益；未构建大规模完整 M8 展开式 MIP。', '']
    (out / 'report.md').write_text('\n'.join(lines), encoding='utf-8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', default='output/experiments/model8_large_probe_20260921')
    p.add_argument('--instances', nargs='+', default=['Set2_100-10-1', 'Set2_200-10-1'])
    p.add_argument('--base-time', type=float, default=20)
    p.add_argument('--exact-time', type=float, default=20)
    p.add_argument('--stage2-time', type=float, default=30)
    p.add_argument('--hard-timeout', type=float, default=90)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--repair-time', type=float, default=8)
    p.add_argument('--max-iterations', type=int, default=1000)
    p.add_argument('--worker', choices=['base_alns', 'base_exact', 'relay', 'relay_bound'])
    p.add_argument('--instance')
    args = p.parse_args()
    if args.worker:
        return worker(args)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    jobs = []
    for instance in args.instances:
        folder = out / instance
        folder.mkdir(parents=True, exist_ok=True)
        for stage in ('base_alns', 'base_exact', 'relay', 'relay_bound'):
            cmd = [sys.executable, '-u', '-m', 'experiments.runs.run_model8_large_probe',
                   '--worker', stage, '--instance', instance, '--output', str(out),
                   '--base-time', str(args.base_time), '--exact-time', str(args.exact_time),
                   '--stage2-time', str(args.stage2_time), '--seed', str(args.seed)]
            cmd += ['--repair-time', str(args.repair_time), '--max-iterations', str(args.max_iterations)]
            print(f'START {instance} {stage}', flush=True)
            started = time.perf_counter()
            with (folder / f'{stage}.log').open('w') as log:
                proc = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                try:
                    code = proc.wait(timeout=args.hard_timeout)
                    status = 'completed' if code == 0 else f'failed_exit_{code}'
                except subprocess.TimeoutExpired:
                    print(f'HARD_TIMEOUT: stopping own worker {instance} {stage}', flush=True)
                    proc.kill()
                    proc.wait()
                    status = 'hard_timeout'
            jobs.append({'instance': instance, 'stage': stage, 'status': status,
                         'wall_seconds': time.perf_counter() - started})
            report(args, jobs)
            print(f"DONE {instance} {stage}: {status} ({jobs[-1]['wall_seconds']:.2f}s)", flush=True)


if __name__ == '__main__':
    main()
