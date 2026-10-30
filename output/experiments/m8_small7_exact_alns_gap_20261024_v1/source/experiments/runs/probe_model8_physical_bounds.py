"""Matched cold-start M8 ablation; preserve the input's complete K/time domain.

The time limit applies to optimization; construction and independent audit
are recorded separately. The callback only observes incumbents/progress;
it never adds constraints, hints, starts, pruning or solver parameter changes.
"""
import argparse
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import gurobipy as gp
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import attach_calendar_decisions
from models.common.cycle.unified_milp import build_unified_model
from models.common.solver_config import accept_milp_audit, read_milp_info
from models.routing.model_8.exact.formulation import build_model
from models.routing.model_8.exact.solution import extract_solution
from tools.configuration.loader import load_routing_case


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    paths = [p for folder in ('models', 'tools/configuration') for p in (ROOT/folder).rglob('*.py')]
    paths.append(Path(__file__))
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(paths)}


def run(args, problem, profile, output):
    begun = time.perf_counter()
    model, ctx = build_unified_model(
        problem, build_fixed=partial(build_model, strengthening=profile),
        compact_e2=args.formulation == 'compact', verbose=False)
    try:
        build_seconds = time.perf_counter() - begun
        assert list(ctx['blocks']) == list(problem.batch_count_candidates)
        assert model.NumStart == 0
        model.Params.Threads = 1
        model.Params.Seed = args.seed
        model.Params.TimeLimit = args.seconds
        solution_limit = getattr(args, 'solution_limit', None)
        if solution_limit is not None:
            model.Params.SolutionLimit = solution_limit
        # Gurobi 11's Windows native file API cannot open this workspace's
        # Unicode absolute path; the project-relative log name is ASCII.
        model.Params.LogFile = os.path.relpath(output / f'{profile}.log')
        model.Params.OutputFlag = 1
        incumbents, progress = [], []

        def observe(m, where):
            if where == gp.GRB.Callback.MIPSOL:
                incumbents.append(dict(seconds=m.cbGet(gp.GRB.Callback.RUNTIME),
                                       objective=m.cbGet(gp.GRB.Callback.MIPSOL_OBJ)))
            elif where == gp.GRB.Callback.MIP:
                elapsed = m.cbGet(gp.GRB.Callback.RUNTIME)
                if not progress or elapsed >= progress[-1]['seconds'] + 15:
                    bound = m.cbGet(gp.GRB.Callback.MIP_OBJBND)
                    progress.append(dict(seconds=elapsed, best_bound=bound if abs(bound) < 1e99 else None,
                                         nodes=m.cbGet(gp.GRB.Callback.MIP_NODCNT)))

        print(json.dumps(dict(stage='optimize', profile=profile, K=list(problem.batch_count_candidates),
                              build_seconds=build_seconds, variables=model.NumVars,
                              constraints=model.NumConstrs+model.NumGenConstrs)), flush=True)
        model.optimize(observe)
        scope = 'full_model_8_all_input_K_joint_cost_integer_time_MILP'
        info = read_milp_info(model, proof_scope=scope, build_time=build_seconds)
        if model.Status == gp.GRB.SOLUTION_LIMIT:
            info['status'] = 'SOLUTION_LIMIT'
        info.update(strengthening=profile, formulation=args.formulation, threads=1, seed=args.seed,
                    optimization_limit_seconds=args.seconds,
                    solution_limit=solution_limit,
                    batch_count_candidates=list(problem.batch_count_candidates), complete_count_domain=True,
                    original_time_tick_seconds=problem.operating_policy.time_tick_seconds,
                    observational_callback=True, custom_cuts=False, mip_start_supplied=False,
                    first_solver_incumbent_seconds=incumbents[0]['seconds'] if incumbents else None,
                    first_solver_incumbent_is_independently_audited=False,
                    incumbents=incumbents, progress=progress,
                    block_bounds={str(k): b['context']['physical_bounds'] for k, b in ctx['blocks'].items()})
        if model.SolCount:
            k = next(k for k, selector in ctx['selectors'].items() if selector.X > .5)
            block = ctx['blocks'][k]
            result = extract_solution(block['problem'], model, block['context'], dict(info))
            boundaries = [b['start'] for b in result['batches']] + [block['problem'].operating_policy.cycle_end]
            attach_calendar_decisions(problem, result, k, boundaries)
            audit_begun = time.perf_counter()
            result['audit'] = audit_solution(problem, result)
            accept_milp_audit(result, model.ObjVal)
            assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
            assert result['info']['objective'] is not None
            info.update(objective=result['info']['objective'], mip_gap=result['info']['mip_gap'],
                        selected_K=k, pool=result['container_pool_size'],
                        audit_valid=True, periodic_replay_valid=True,
                        offline_audit_seconds=time.perf_counter()-audit_begun,
                        max_constraint_violation=model.ConstrVio, max_integer_violation=model.IntVio)
            write(output/f'{profile}_result.json', result)
        info['wall_seconds'] = time.perf_counter() - begun
        write(output/f'{profile}_summary.json', info)
        print(json.dumps(dict(stage='complete', profile=profile, status=info['status'],
                              objective=info['objective'], bound=info['best_bound'],
                              first_solver_incumbent_seconds=info['first_solver_incumbent_seconds'])), flush=True)
        return info
    finally:
        model.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=300)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--solution-limit', type=int)
    parser.add_argument('--formulation', choices=('compact', 'expanded'), default='compact')
    parser.add_argument('--profiles', nargs='+', choices=('none', 'physical', 'physical_time'), default=['none', 'physical'])
    args = parser.parse_args()
    if args.seconds <= 0 or (args.solution_limit is not None and args.solution_limit < 1):
        parser.error('--seconds and --solution-limit must be positive')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config, fleet = load_routing_case(args.case, use_pool_config=False)
    problem = load_instance(config, fleet)
    assert problem.model_number == 8
    inputs = {str(args.case.resolve()): sha(args.case)}
    for key in ('data', 'routing_config', 'economic_config', 'orders', 'operating_policy',
                'parameter_contract', 'batch_count_certificate', 'dataset_metadata'):
        if config.get(key) and Path(config[key]).is_file():
            inputs[str(Path(config[key]).resolve())] = sha(Path(config[key]))
    manifest = dict(case=str(args.case.resolve()), K=list(problem.batch_count_candidates),
                    input_hashes=inputs, source_hashes=source_hashes(), profiles=args.profiles,
                    seconds=args.seconds, formulation=args.formulation, seed=args.seed,
                    solution_limit=args.solution_limit,
                    threads=1, time_budget_scope='optimization_only_build_and_audit_separate',
                    external_starts=False, custom_cuts=False)
    write(output/'manifest.json', manifest)
    rows = []
    for profile in args.profiles:
        rows.append(run(args, problem, profile, output))
        write(output/'comparison.json', rows)
        assert source_hashes() == manifest['source_hashes'], 'Source changed during ablation'
        assert all(sha(Path(path)) == digest for path, digest in inputs.items()), 'Input changed'
    lines = ['# 模型八必要物理约束对照', '',
             f"K={manifest['K']}，原输入整数时钟，{args.formulation}表达，单线程，种子{args.seed}。",
             f'每组优化上限{args.seconds:g}秒，建模及审计另计；均冷启动。仅用只读回调记录求解器事件。', '',
             '| 增强 | 状态 | 成本 | 下界 | GAP | 首个求解器可行解秒 | 建模秒 | 优化秒 |',
             '|---|---|---:|---:|---:|---:|---:|---:|']
    def fmt(value):
        return '—' if value is None else f'{value:.4f}'
    for row in rows:
        lines.append('| ' + ' | '.join([row['strengthening'], row['status'], fmt(row['objective']),
                      fmt(row['best_bound']), fmt(row['mip_gap']), fmt(row['first_solver_incumbent_seconds']),
                      fmt(row['build_time']), fmt(row['mip_runtime'])]) + ' |')
    lines += ['', '首个可行解时间为Gurobi接收整数解的回调时间；仅最终方案独立审计，不能称为首次业务审计通过时间。',
              '保留输入K域、时间精度、资源与费用；无人工固定日历、路线或分组。physical_time可由必要时间条件推导订单必须属于第一批。未调用ALNS。',
              '每个限时下界覆盖本输入全部K；TIME_LIMIT无解不表示不可行。只代表本次算例/种子/预算。', '']
    (output/'report.md').write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
