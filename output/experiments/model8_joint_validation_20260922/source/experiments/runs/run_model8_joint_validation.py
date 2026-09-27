"""Validate current joint M8 without loading or fixing any previously solved M3 routes.

Constructed mechanism and one original small case: independent M3/no-relay/relay
exact solves. All four cases: matched standalone joint ALNS with/without relay.
Small wall budgets establish execution/semantics, not large-instance optimality.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = 'output/experiments/model8_joint_validation_20260922'


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temporary.replace(path)


def load_problem(instance):
    from models.routing.common.task_builder import build_tasks_for_model
    if instance == 'mechanism':
        from experiments.scenarios.model8_capacity import make_capacity_example
        p = make_capacity_example()
        source, config = ROOT / 'experiments/scenarios/model8_capacity.py', None
    else:
        from models.routing.common.data_loader import parse_dataset
        from models.routing.common.domain.instance import ProblemInstance
        size = 'small' if instance.startswith('Set1') else 'large'
        source = ROOT / f'data/zhou_later_pickup/{size}/standard_same_day/{instance}.csv'
        config = ROOT / f'config/routing/validation/zhou_{size}.json'
        fleet = json.loads(config.read_text())
        nodes, _ = parse_dataset(str(source))
        p = ProblemInstance(nodes, fleet, speed=fleet['speed'])
        p.aggregate_demands_from_pre_assignment()
    a, b = build_tasks_for_model(p, 8)
    return p, a, b, source, config


def worker(args):
    from models.routing.variants.model_8.state import Evaluator, state_from_routes
    p, a, b, _, _ = load_problem(args.instance)
    count = p.fleet.e2_per_sat_count
    common = dict(problem=p, e1_tasks=a, e2_tasks_by_sat=b,
                  vehicles_per_satellite=count, verbose=False, return_info=True)
    if args.method == 'm3_exact':
        from models.routing.variants.model_3.exact_solver import solve_exact
        x, y, info = solve_exact(**common, time_limit=args.exact_time,
            return_incumbent_on_time_limit=True, gurobi_params={'Threads': 1})
    elif args.method.startswith('exact'):
        from models.routing.variants.model_8.exact_solver import solve_exact
        x, y, info = solve_exact(**common, time_limit=args.exact_time,
            allow_transfers=args.method.endswith('_relay'), return_incumbent_on_time_limit=True,
            gurobi_params={'Threads': 1, 'Seed': args.seed})
    else:
        from models.routing.alns.variants.model_8 import solve_alns
        x, y, info = solve_alns(**common, max_time=args.time, max_iterations=1000000,
            allow_transfers=args.method.endswith('_relay'), seed=args.seed)
    if x is None or not info.get('feasible', info.get('objective') is not None):
        raise RuntimeError(f'No solution: {info}')
    state = info.get('state') or state_from_routes(p, count, (x, y))
    audit = Evaluator(p, a, b, count).audit(state)
    if not math.isclose(audit['objective'], info['objective'], abs_tol=1e-5):
        raise RuntimeError('Objective mismatch')
    if args.method == 'm3_exact' or args.method.endswith('_control'):
        if state['transfers']:
            raise RuntimeError('No-relay control contains handoffs')
    if args.method != 'm3_exact' and any(k in info for k in ('baseline', 'stage1', 'stage2', 'conditional_proven_optimal')):
        raise RuntimeError('Joint entry leaked two-stage semantics')
    write(ROOT / args.output / args.instance / f'{args.method}_{args.seed}.json', {
        'e1_routes': x, 'e2_routes': y, 'state': state, 'info': info, 'audit_valid': True})


def analyze(folder, settings, jobs):
    from models.routing.variants.model_8.state import Evaluator
    manifest = json.loads((folder / 'manifest.json').read_text())
    for name, sha in manifest['source_sha256'].items():
        for path in (ROOT / name, folder / 'source' / name):
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise RuntimeError(f'Source changed: {path}')
    rows = []
    for job in jobs:
        p, a, b, source, config = load_problem(job['instance'])
        for path in (source, config):
            if path is not None and hashlib.sha256(path.read_bytes()).hexdigest() != manifest['input_sha256'][str(path.relative_to(ROOT))]:
                raise RuntimeError('Input changed')
        path = folder / job['instance'] / f"{job['method']}_{job['seed']}.json"
        r = json.loads(path.read_text())
        e = Evaluator(p, a, b, p.fleet.e2_per_sat_count)
        audit = e.audit(r['state'])
        info = r['info']
        if not math.isclose(audit['objective'], info['objective'], abs_tol=1e-5):
            raise RuntimeError('Reloaded objective mismatch')
        rows.append({'instance': job['instance'], 'method': job['method'], 'seed': job['seed'],
                     'objective': info['objective'], 'proven_optimal': bool(info.get('proven_optimal')),
                     'best_bound': info.get('best_bound'), 'mip_gap': info.get('mip_gap'),
                     'initial_objective': info.get('initial_objective'),
                     'initialization_seconds': info.get('initialization_seconds'),
                     'e1_changed': info.get('e1_routes_changed'), 'iterations': info.get('iterations'),
                     'handoffs': len(r['state']['transfers']),
                     'split_deposits': sum(len(x['collections']) > 1 for x in audit['handoff_batches'])})
    for instance in settings['instances']:
        for seed in settings['seeds']:
            candidates = [r for r in rows if r['instance'] == instance and r['seed'] == seed and r['method'].startswith('alns')]
            if len(candidates) != 2 or not math.isclose(candidates[0]['initial_objective'], candidates[1]['initial_objective'], abs_tol=1e-6):
                raise RuntimeError('Standalone constructive starts are not matched')
    write(folder / 'summary.json', {'runs': rows, 'audited_solutions': len(rows), 'scope': 'joint implementation validation, finite budget'})
    write(folder / 'verification.json', {'status': 'PASS', 'audited_solutions': len(rows), 'matched_initial_objectives': True})
    lines = ['# M8 v3 联合优化验证', '', '## Material Passport', '',
             '- 实际运行：M8 默认入口不求 M3、不读取旧 M3 结果，不固定任何求解得到的路线。',
             '- 精确对照中的 M3 单独求解，其结果不会传入 M8；M8 无传递对照可自由优化 E1/E2。',
             '- mechanism 为构造机制例，其余为未经需求/容量/时间窗改动的原始算例。',
             f"- ALNS 每次 {settings['time']} 秒，种子 {settings['seeds']}；小例精确预算 {settings['exact_time']} 秒。",
             '- 两个 ALNS 分支使用相同构造器和完整车队上限；从原始数据独立构造相同起点。',
             '- 本轮检验新联合语义与实现可行性，短预算大算例不能作为充分收益或最优性结论。', '',
             '## 小规模精确结果', '', '| 实例 | 方法 | 距离 | 全局最优已证明 |', '|---|---|---:|---|']
    for r in rows:
        if not r['method'].startswith('alns'):
            lines.append(f"| {r['instance']} | {r['method']} | {r['objective']:.6f} | {'是' if r['proven_optimal'] else '否'} |")
    lines += ['', '## 联合 ALNS 逐次结果', '', '| 实例 | 方法 | 种子 | 初始距离 | 最终距离 | E1 改变 | 交接对数 |',
              '|---|---|---:|---:|---:|---|---:|']
    for r in rows:
        if r['method'].startswith('alns'):
            lines.append(f"| {r['instance']} | {r['method']} | {r['seed']} | {r['initial_objective']:.6f} | {r['objective']:.6f} | "
                         f"{'是' if r['e1_changed'] else '否'} | {r['handoffs']} |")
    lines += ['', f"全部 {len(rows)} 个保存方案重新加载审计通过。检查 E1/E2 全部任务、载荷、时间窗、两级衔接、一次转交与距离。",
              '旧的两阶段结果保持独立，不把其改善率归入本版本。当前规则下 M8 与 M6 精确可行域相同，区别在求解流程和容量协作搜索。', '',
              '[原始数值](summary.json) · [审计](verification.json) · [命令与状态](jobs.json)']
    (folder / 'findings.md').write_text('\n'.join(lines)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instances', nargs='+', default=['mechanism', 'Set1_7-2-1b', 'Set2_100-5-2', 'Set2_200-10-2'])
    parser.add_argument('--seeds', nargs='+', type=int, default=[42, 7, 19])
    parser.add_argument('--time', type=float, default=5)
    parser.add_argument('--exact-time', type=float, default=10)
    parser.add_argument('--output', default=DEFAULT_OUTPUT)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--instance')
    parser.add_argument('--method', choices=['m3_exact', 'exact_control', 'exact_relay', 'alns_control', 'alns_relay'])
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    folder = ROOT / args.output
    folder.mkdir(parents=True, exist_ok=False)
    sources = list((ROOT / 'models/routing').rglob('*.py')) + [Path(__file__).resolve(), ROOT / 'tests/routing/test_model8_joint.py']
    hashes, inputs = {}, {}
    for source in sources:
        rel = source.relative_to(ROOT)
        dest = folder / 'source' / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        hashes[str(rel)] = hashlib.sha256(source.read_bytes()).hexdigest()
    for instance in args.instances:
        _, _, _, data, config = load_problem(instance)
        for path in (data, config):
            if path is not None:
                inputs[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
                dest = folder / 'inputs' / path.relative_to(ROOT)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
    write(folder / 'manifest.json', {'settings': vars(args), 'python': sys.version,
                                   'source_sha256': hashes, 'input_sha256': inputs})
    specs = []
    for instance in args.instances:
        if instance == 'mechanism' or instance.startswith('Set1'):
            specs += [(instance, method, 42) for method in ('m3_exact', 'exact_control', 'exact_relay')]
        specs += [(instance, method, seed) for seed in args.seeds for method in ('alns_control', 'alns_relay')]
    started = time.perf_counter()

    def launch(instance, method, seed):
        cmd = [sys.executable, '-m', 'experiments.runs.run_model8_joint_validation', '--worker',
               '--instance', instance, '--method', method, '--seed', str(seed),
               '--time', str(args.time), '--exact-time', str(args.exact_time), '--output', args.output]
        path = folder / instance / f'{method}_{seed}.log'
        path.parent.mkdir(exist_ok=True)
        with path.open('w') as log:
            try:
                r = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                   timeout=max(args.time, args.exact_time)+45)
                status = 'completed' if r.returncode == 0 else f'failed:{r.returncode}'
            except subprocess.TimeoutExpired:
                status = 'hard_timeout'
        return {'instance': instance, 'method': method, 'seed': seed, 'status': status, 'command': cmd}
    jobs = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(launch, *spec) for spec in specs]
        for future in as_completed(futures):
            job = future.result()
            jobs.append(job)
            write(folder / 'jobs.json', {'jobs': jobs, 'elapsed': time.perf_counter()-started})
            print(json.dumps({k:v for k,v in job.items() if k != 'command'}), flush=True)
    if any(j['status'] != 'completed' for j in jobs):
        raise RuntimeError('Validation batch has failures; retain outputs and inspect logs')
    analyze(folder, vars(args), jobs)
    print(f'PASS: {len(jobs)} saved solutions re-audited', flush=True)


if __name__ == '__main__':
    main()
