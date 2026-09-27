# Historical M8 experiments: retain their original two-stage semantics.
"""Reproducible M8 v2 capacity-cooperation experiment with matched controls.

Designed examples establish mechanism only. Original Zhou 100/200 instances
reuse the previous M3 solutions, so old/new M8 share exactly the same baseline.
Every worker has a hard timeout and persists complete routes and audit data.
"""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = 'output/experiments/model8_capacity_20260922'


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def load_problem(instance):
    from models.routing.common.task_builder import build_tasks_for_model
    if instance.startswith('mechanism'):
        from experiments.scenarios.model8_capacity import make_capacity_example
        problem = make_capacity_example(4 if instance.endswith('loose') else 3)
        source = ROOT / 'experiments/scenarios/model8_capacity.py'
    else:
        from models.routing.common.data_loader import parse_dataset
        from models.routing.common.domain.instance import ProblemInstance
        source = ROOT / 'data/zhou_later_pickup/large/standard_same_day' / f'{instance}.csv'
        nodes, _ = parse_dataset(str(source))
        fleet = json.loads((ROOT / 'config/routing/validation/zhou_large.json').read_text())
        problem = ProblemInstance(nodes, fleet, speed=fleet['speed'])
        problem.aggregate_demands_from_pre_assignment()
    e1, e2 = build_tasks_for_model(problem, 8)
    return problem, e1, e2, source


def worker(args):
    problem, e1, e2, source = load_problem(args.instance)
    folder = ROOT / args.output / args.instance
    started = time.perf_counter()
    if args.worker == 'base':
        if args.instance.startswith('mechanism'):
            from models.routing.variants.model_3.exact_solver import solve_exact
            a, b, info = solve_exact(problem, e1, e2, problem.fleet.e2_per_sat_count,
                                    time_limit=20, verbose=False, return_info=True,
                                    return_incumbent_on_time_limit=True,
                                    gurobi_params={'Threads': 2, 'Seed': 42})
            provenance = 'designed_mechanism_example'
        else:
            candidates = []
            previous = ROOT / 'output/experiments/model8_large_probe_20260921' / args.instance
            for name in ('base_exact.json', 'base_alns.json'):
                candidate = json.loads((previous / name).read_text())
                if candidate['e1_routes'] is not None:
                    candidates.append(candidate)
            selected = min(candidates, key=lambda r: r['info']['objective'])
            a, b, info = selected['e1_routes'], selected['e2_routes'], selected['info']
            provenance = str(previous)
        if a is None:
            raise RuntimeError('No feasible baseline')
        from models.routing.variants.model_8.two_stage_v2.baseline import prepare_baseline
        count = max(len(r) for r in b.values())
        checked = prepare_baseline(problem, e1, e2, count, (a, b))
        result = {'e1_routes': a, 'e2_routes': b, 'info': info,
                  'effective_vehicle_count': count, 'source': str(source),
                  'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                  'baseline_fingerprint': checked['route_fingerprint'], 'provenance': provenance}
        dump(folder / 'base.json', result)
    else:
        base = json.loads((folder / 'base.json').read_text())
        shared = dict(problem=problem, e1_tasks=e1, e2_tasks_by_sat=e2,
                      vehicles_per_satellite=base['effective_vehicle_count'],
                      base_routes=(base['e1_routes'], base['e2_routes']),
                      base_info=base['info'], verbose=False, return_info=True,
                      allow_transfers=args.worker.endswith('relay'))
        if args.worker.startswith('exact'):
            from models.routing.variants.model_8.two_stage_v2.exact_solver import solve_exact
            a, b, info = solve_exact(**shared, time_limit=args.stage2_time,
                                    return_incumbent_on_time_limit=True,
                                    gurobi_params={'Threads': 2, 'Seed': args.seed})
        else:
            from models.routing.variants.model_8.two_stage_v2.alns import solve_alns
            a, b, info = solve_alns(**shared, max_time=args.stage2_time,
                                   max_iterations=1000000, seed=args.seed,
                                   repair_time_limit=.5, max_remove=3)
        result = {'e1_routes': a, 'e2_routes': b, 'info': info,
                  'seed': args.seed, 'stage2_budget': args.stage2_time,
                  'wall_seconds': time.perf_counter() - started,
                  'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                                  / (1024 ** 2 if sys.platform == 'darwin' else 1024)}
        dump(folder / f'{args.worker}_{args.seed}.json', result)
    print(json.dumps({'instance': args.instance, 'method': args.worker,
                      'objective': result['info']['objective'],
                      'transfers': result['info'].get('transshipment_count', 0),
                      'iterations': result['info'].get('iterations'),
                      'wall_seconds': time.perf_counter() - started}), flush=True)


def make_report(args, jobs):
    folder = ROOT / args.output
    rows = []
    for instance in args.instances:
        base_path = folder / instance / 'base.json'
        if not base_path.exists():
            continue
        base = json.loads(base_path.read_text())
        methods = ('exact_control', 'exact_relay', 'alns_control', 'alns_relay')
        for path in sorted((folder / instance).glob('*.json')):
            if not path.stem.startswith(methods):
                continue
            r = json.loads(path.read_text())
            i = r['info']
            rows.append({'instance': instance, 'method': path.stem, 'seed': r['seed'],
                         'baseline': base['info']['objective'],
                         'base_proven': bool(base['info'].get('proven_optimal')),
                         'objective': i['objective'], 'saving': i['total_improvement'],
                         'saving_percent': 100 * i['total_improvement'] / base['info']['objective'],
                         'transfers': i['transshipment_count'], 'boxes': i.get('transferred_boxes', 0),
                         'iterations': i.get('iterations'), 'conditional_optimal': i['conditional_proven_optimal'],
                         'runtime': i['runtime'], 'memory_mib': r['peak_rss_mib'],
                         'capacity_candidates': i.get('capacity_blocked_candidates'),
                         'feasible_relay_repairs': i.get('feasible_relay_repairs'),
                         'fingerprint': i['baseline']['route_fingerprint']})
    sources = ['models/routing/variants/model_8/two_stage_v2/alns.py',
               'models/routing/variants/model_8/two_stage_v2/state.py',
               'models/routing/variants/model_8/two_stage_v2/model.py',
               'models/routing/variants/model_8/two_stage_v2/exact_solver.py',
               'experiments/scenarios/model8_capacity.py']
    hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in sources}
    dump(folder / 'summary.json', {'settings': vars(args), 'jobs': jobs,
                                  'results': rows, 'source_sha256': hashes, 'python': sys.version})
    lines = ['# M8 容量协作与回收重分配实验', '', '## Material Passport', '',
             '- 状态：实际执行；构造例与原始 Zhou 数据分别报告。',
             '- 目标：同样任务、同仓、既有车辆下最小化两级总距离。',
             '- 固定 E1 路线及配送车辆/顺序，释放回收归属/顺序；异步交接同质空箱，每箱最多一次。',
             '- 基础解未证明最优时明确标记；第二阶段精确证书只针对给定配送骨架。',
             '- control 与 relay 的回收调整权限相同，仅 relay 允许空箱交接。',
             '- ALNS 同预算差值为算法比较，不是因果或全局最优传递价值；总改善不全部归因于传递。',
             f'- 随机种子：{args.seeds}；每个第二阶段预算 {args.stage2_time}s；硬超时 {args.hard_timeout}s。',
             '- 证据：各实例下 JSON 保存基线指纹、完整路线、数量、时刻、逐段载荷、搜索历史及独立校验。',
             '- 历史 M8 结果位于 `output/experiments/model8_large_probe_20260921`，旧实现保留在 `model_8/legacy`。',
             '', '| 实例 | 方法/种子 | 基础距离 | M3证明 | 最终距离 | 节省% | 交接次数 | 迭代数 | 条件最优 | 耗时s |',
             '|---|---|---:|---|---:|---:|---:|---:|---|---:|']
    for r in rows:
        lines.append(f"| {r['instance']} | {r['method']} | {r['baseline']:.6f} | {r['base_proven']} | "
                     f"{r['objective']:.6f} | {r['saving_percent']:.4f} | {r['transfers']} | "
                     f"{r['iterations']} | {r['conditional_optimal']} | {r['runtime']:.2f} |")
    lines += ['', '## 完成与超时', ''] + [f"- {j['instance']} / {j['method']} / {j['seed']}: {j['status']} ({j['seconds']:.2f}s)" for j in jobs]
    (folder / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instances', nargs='+', default=['mechanism_tight', 'mechanism_loose', 'Set2_100-10-1', 'Set2_200-10-1'])
    parser.add_argument('--seeds', nargs='+', type=int, default=[42, 7, 19])
    parser.add_argument('--stage2-time', type=float, default=30)
    parser.add_argument('--hard-timeout', type=float, default=90)
    parser.add_argument('--output', default=DEFAULT_OUTPUT)
    parser.add_argument('--worker', choices=['base', 'exact_control', 'exact_relay', 'alns_control', 'alns_relay'])
    parser.add_argument('--instance')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    jobs = []
    for instance in args.instances:
        methods = [('base', 42)]
        if instance.startswith('mechanism'):
            methods += [('exact_control', 42), ('exact_relay', 42)]
        methods += [(method, seed) for seed in args.seeds for method in ('alns_control', 'alns_relay')]
        for method, seed in methods:
            target = ROOT / args.output / instance
            target.mkdir(parents=True, exist_ok=True)
            cmd = [sys.executable, '-m', 'experiments.runs.run_model8_capacity_probe',
                   '--worker', method, '--instance', instance, '--seed', str(seed),
                   '--stage2-time', str(args.stage2_time), '--output', args.output]
            started = time.perf_counter()
            with (target / f'{method}_{seed}.log').open('w') as log:
                try:
                    done = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                          timeout=args.hard_timeout, check=False)
                    status = 'completed' if done.returncode == 0 else f'failed:{done.returncode}'
                except subprocess.TimeoutExpired:
                    status = 'hard_timeout'
            job = {'instance': instance, 'method': method, 'seed': seed, 'status': status,
                   'seconds': time.perf_counter() - started}
            jobs.append(job)
            print(json.dumps(job), flush=True)
            make_report(args, jobs)
            if status != 'completed':
                raise RuntimeError(f'Worker did not complete: {job}; inspect log')
    print(ROOT / args.output / 'report.md', flush=True)


if __name__ == '__main__':
    main()
