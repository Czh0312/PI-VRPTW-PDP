"""Opt-in M1--M4 warm-start pilot; the default exact solver stays untouched."""
from __future__ import annotations

import argparse
import csv
import hashlib
from importlib import import_module
import json
import math
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[2]
PRIOR = Path('output/experiments/small10_m1_m4_exact300_20261023_v1')


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def finite(value):
    return float(value) if math.isfinite(value) and abs(value) < 1e90 else None


def source_hashes():
    paths = [p for base in (ROOT/'models', ROOT/'tools/configuration')
             for p in base.rglob('*.py')]
    paths += [Path(__file__), ROOT/'experiments/analysis/single_batch_mip_start.py']
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def trial(case, number, mode, args, folder):
    import gurobipy as gp
    from gurobipy import GRB
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.common.cycle.state import empty_result
    from models.common.solver_config import read_milp_info, accept_milp_audit
    from models.routing.service import solve_routing
    from experiments.analysis.single_batch_mip_start import apply_mip_start

    folder.mkdir(parents=True, exist_ok=False)
    dataset = getattr(args, 'dataset', 'data/formal_benchmark27_spacetime_v3')
    case_file = ROOT/dataset/f'model_{number}/small/{case}/case.json'
    config, fleet = load_routing_case(str(case_file), use_pool_config=False)
    problem = load_instance(config, fleet)
    write(folder/'config.json', config)
    begun = time.perf_counter()
    seed, seed_info = None, dict(mode=mode, valid=False)
    if mode == 'alns':
        seed = solve_routing(model=number, method='alns', problem=problem,
            alns_time=args.alns_seconds, alns_iter=10**9, seed=args.seed)
        seed_info.update(seconds=time.perf_counter()-begun,
            valid=bool(seed['info'].get('objective') is not None and audit_solution(problem, seed)['valid']))
        write(folder/'initial_result.json', seed)
        if not seed_info['valid']:
            seed = None
    elif mode == 'saved':
        source_model = number-1 if number in (2, 4) else number
        path = ROOT/args.prior/'runs'/f'{case}_m{source_model}_exact_300s_s{args.seed}'/'attempt_001/result.json'
        seed = read(path)
        seed_info.update(source=str(path), source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            preparation_cost_included=False, limitation='Diagnostic supplied incumbent; its historical search time is excluded.')
    seed_seconds = time.perf_counter()-begun
    build = import_module(f'models.routing.model_{number}.exact.formulation').build_model
    build_options = dict(compress_routes=True, deadline=begun+args.total_seconds)
    if number == 4:
        build_options['strengthening'] = getattr(args, 'm4_strengthening', 'none')
    model, context = build(problem, **build_options)
    try:
        model.Params.LogToConsole = 0
        model.Params.LogFile = (folder/'gurobi.log').relative_to(ROOT).as_posix()
        model.Params.OutputFlag = 1
        model.Params.Threads = 1
        model.Params.Seed = args.seed
        if seed is not None:
            mapped = apply_mip_start(problem, model, context, seed)
            seed_info.update(valid=True, objective=mapped['objective'],
                variables_populated=mapped['variables_populated'])
            write(folder/'initial_audit.json', mapped['physical_audit'])
            write(folder/'canonical_initial.json', mapped['canonical_result'])
        write(folder/'initial_metadata.json', seed_info)
        build_seconds = time.perf_counter()-begun
        trajectory, last_record = [], [-math.inf]
        def observe(m, where):
            elapsed = time.perf_counter()-begun
            if where == GRB.Callback.MIPSOL:
                trajectory.append(dict(elapsed=elapsed, event='incumbent',
                    objective=finite(m.cbGet(GRB.Callback.MIPSOL_OBJ)),
                    best_bound=finite(m.cbGet(GRB.Callback.MIPSOL_OBJBND))))
            elif where == GRB.Callback.MIP and elapsed-last_record[0] >= 10:
                last_record[0] = elapsed
                trajectory.append(dict(elapsed=elapsed, event='bound_progress',
                    objective=finite(m.cbGet(GRB.Callback.MIP_OBJBST)),
                    best_bound=finite(m.cbGet(GRB.Callback.MIP_OBJBND))))
        model.Params.TimeLimit = max(.001, args.total_seconds-build_seconds)
        model.optimize(observe)
        result = empty_result(problem, 'exact_warm_start_trial')
        result['info'] = read_milp_info(model, build_time=build_seconds,
            proof_scope=f'full_model_{number}_integer_time_MILP')
        result['info'].update(mip_start_supplied=seed is not None,
            experiment='single_batch_warm_start_trial', callback_role='observation_only',
            threads=1, formulation='compact', seed=args.seed)
        if number == 4:
            result['info']['strengthening'] = context['strengthening']
        if model.SolCount:
            extract = import_module(f'models.routing.model_{number}.exact.solution').extract_solution
            extract(problem, model, context, result)
            result['audit'] = audit_solution(problem, result)
            accept_milp_audit(result, float(model.ObjVal))
        result['info']['runtime'] = time.perf_counter()-begun
        write(folder/'result.json', result)
        write(folder/'trajectory.json', trajectory)
        prior_path = ROOT/args.prior/'runs'/f'{case}_m{number}_exact_300s_s{args.seed}'/'attempt_001/row.json'
        prior = read(prior_path) if prior_path.exists() else {}
        incumbent_times = [t['elapsed'] for t in trajectory if t['event']=='incumbent']
        row = dict(case=case, model=number, mode=mode, budget=args.total_seconds,
            strengthening=getattr(args, 'm4_strengthening', 'none') if number == 4 else 'none',
            initial_seconds=seed_seconds, initial_valid=seed_info['valid'],
            initial_objective=seed_info.get('objective'),
            status=result['info']['status'], valid=result.get('audit', {}).get('valid', False),
            proven_optimal=result['info']['proven_optimal'], objective=result['info']['objective'],
            best_bound=result['info']['best_bound'], mip_gap=result['info']['mip_gap'],
            first_solver_incumbent_seconds=min(incumbent_times) if incumbent_times else None,
            total_seconds=result['info']['runtime'], build_and_seed_seconds=build_seconds,
            mip_seconds=model.Runtime, prior_cold_objective=prior.get('objective'),
            prior_cold_gap=prior.get('mip_gap'), prior_cold_valid=prior.get('valid'))
    finally:
        model.dispose()
    log = (folder/'gurobi.log').read_text(encoding='utf-8', errors='replace')
    row['mip_start_accepted'] = (('Loaded user MIP start' in log or 'User MIP start produced solution' in log)
                                 if seed is not None else None)
    write(folder/'row.json', row)
    return row


def report(out, rows):
    write(out/'rows.json', rows)
    with (out/'summary.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = ['# 单批模型热启动试验', '',
        '独立实验入口；默认直接Gurobi、冻结数据和历史结果均保持原样。',
        'alns组：初解生成、审计、映射、建模和MILP共享总预算；saved组为已有解诊断，其历史求解时间未计入。',
        '记录回调仅观察，不增加割、约束或外部下界；时间是本次机器负载下的墙钟时间。',
        'prior为此前相同种子、单线程、compact、300秒冷启动记录，运行时刻不同，不作为严格同机同步性能结论。', '',
        '| 算例 | 模型 | 方式 | 初解 | Start接受 | 首个求解器解秒 | 最终目标 | GAP | 最优证明 | 历史冷启动目标 / GAP |',
        '|---|---:|---|---:|---|---:|---:|---:|---|---|']
    def num(value):
        return '—' if value is None else f'{value:.4f}'
    for r in rows:
        lines.append(f'| {r["case"]} | M{r["model"]} | {r["mode"]} | {num(r["initial_objective"])} | '
            f'{r["mip_start_accepted"]} | {num(r["first_solver_incumbent_seconds"])} | '
            f'{num(r["objective"])} | {num(r["mip_gap"])} | {r["proven_optimal"]} | '
            f'{num(r["prior_cold_objective"])} / {num(r["prior_cold_gap"])} |')
    lines += ['', 'GAP为比例。每个结果均保存初解、最终解、原始日志、观察轨迹和独立双周期审计。', '']
    (out/'report.md').write_text('\n'.join(lines), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--dataset', default='data/formal_benchmark27_spacetime_v3')
    parser.add_argument('--cases', nargs='+', default=['S02', 'S04', 'S06'])
    parser.add_argument('--models', nargs='+', type=int, choices=range(1, 5), default=[4])
    parser.add_argument('--modes', nargs='+', choices=['cold', 'alns', 'saved'], default=['alns'])
    parser.add_argument('--total-seconds', type=float, default=300)
    parser.add_argument('--alns-seconds', type=float, default=30)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--m4-strengthening', choices=['none','bounds','capacity','combined','activation','capacity_activation'], default='none')
    parser.add_argument('--prior', type=Path, default=PRIOR)
    args = parser.parse_args()
    if not 0 < args.alns_seconds < args.total_seconds:
        parser.error('Require 0 < alns-seconds < total-seconds')
    if args.m4_strengthening != 'none' and any(number != 4 for number in args.models):
        parser.error('M4 strengthening requires --models 4')
    out = (ROOT/args.output).resolve()
    out.relative_to(ROOT)
    if out.exists() and any(out.iterdir()):
        raise ValueError('Use a new output directory; completed trials are immutable')
    out.mkdir(parents=True, exist_ok=True)
    before = source_hashes()
    write(out/'protocol.json', dict(cases=args.cases, models=args.models, modes=args.modes,
        total_seconds=args.total_seconds, alns_seconds=args.alns_seconds, seed=args.seed,
        m4_strengthening=args.m4_strengthening,
        source_sha256=before, dataset=args.dataset,
        threads=1, formulation='compact', objective='distance',
        source_comparison='Default solver unchanged; opt-in Start mapping and observation callback only'))
    rows = []
    try:
        for case in args.cases:
            for number in args.models:
                for mode in args.modes:
                    print(f'START {case} M{number} {mode}', flush=True)
                    row = trial(case, number, mode, args, out/f'{case}_m{number}_{mode}')
                    rows.append(row)
                    report(out, rows)
                    print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        write(out/'integrity.json', dict(source_unchanged=before == source_hashes(), completed=len(rows)))


if __name__ == '__main__':
    main()
