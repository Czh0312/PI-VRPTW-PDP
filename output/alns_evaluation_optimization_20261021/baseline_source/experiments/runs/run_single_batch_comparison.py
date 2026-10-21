"""Freeze one B=1 case, compare M1-M4, and price each solver/seed separately."""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.configuration.loader import load_routing_case, load_json, resolve_project_path
from experiments.runs.run_multibatch_cycle import (
    INPUT_KEYS, freeze_inputs, source_snapshot, run_worker, write_json)
from models.common.cycle import load_instance, MultiBatchPolicy
from models.common.cycle.state import empty_result
from models.cost.single_batch import compare_single_batch


def report(comparisons, rows, out, parameters):
    lines = ['# 单批次 M1–M4 对照', '',
             f"费用来源：`{parameters['source_type']}`；单位：`{parameters['currency']}`。",
             '这是固定输入和参数下的条件分析。当前演示参数不能证明真实业务盈利。', '',
             '| 求解器/种子 | 模型 | 状态 | 审计 | 距离 | 距离最优已证明 |',
             '| --- | --- | --- | --- | ---: | --- |']
    for row in rows:
        lines.append(f"| {row['solver']}/{row['seed']} | M{row['model']} | {row['status']} | "
                     f"{row['valid']} | {row['objective']} | {row['proven_optimal']} |")
    for name, comparison in comparisons.items():
        lines += ['', f'## {name}', '',
                  '| 模型 | 每周期运营费用 | 初始箱池投资 |', '| --- | ---: | ---: |']
        for model, cost in comparison['costs'].items():
            lines.append(f"| M{model} | {cost['period_operating_cost']:.6f} | {cost['initial_pool_investment']:.6f} |")
        lines += ['', '| 对照 | 评价周期数 | 循环方案累计节省 | 期内首次回本周期 | 此后至期末不再亏损的起点 |',
                  '| --- | ---: | ---: | ---: | ---: |']
        for pair, data in comparison['comparisons'].items():
            if not data['available']:
                lines.append(f'| {pair} | — | 缺少双方可行方案 | — | — |')
            else:
                lines.append(f"| {pair} | {data['evaluation_cycles']} | {data['horizon_saving']:.6f} | "
                             f"{data['first_break_even_within_horizon']} | {data['no_more_expensive_from_cycle_through_horizon']} |")
        lines += ['', '固定路线的包装价格 × 容器寿命敏感性：', '',
                  '| 对照 | 一次性包装单价 | 箱池寿命（周期） | 评价期累计节省 | 首次回本周期 |',
                  '| --- | ---: | ---: | ---: | ---: |']
        for data in comparison['sensitivity']:
            lines.append(f"| M{data['single_use_model']}/M{data['reusable_model']} | {data['single_use_package_price']:g} | "
                         f"{data['pool_lifetime_cycles']} | {data['horizon_saving']:.6f} | {data['first_break_even_within_horizon']} |")
    lines += ['', '成本包含距离、占用工时、实际启用车辆和包装相关费用。一次性包装每周期重新购买；',
              '循环箱按固定池购入，并按声明寿命整池更新。累计费用不再叠加摊销。负节省表示循环方案更贵。',
              '各方法/种子分别报告，不从多个运行中挑出一个经济最有利方案。',
              '距离最优不等于费用最优；固定路线采用最早可行时间表，尚未优化工时。',
              '同质包装的正向重量/体积暂取相同。未计随机损耗、资金折现、融资、通胀和残值。',
              '完整逐周期费用、寿命阈值和证明范围见 comparison_*.json；失败也保存在 runs.csv。',
              '单批次大周期的时长由输入决定，不自动等于一天。']
    (out/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', default='config/experiments/examples/single_batch_demo.json')
    parser.add_argument('--solver', choices=('both', 'exact', 'alns'), default='both')
    parser.add_argument('--seeds', nargs='+', type=int, default=[42])
    parser.add_argument('--time', type=float, default=2)
    parser.add_argument('--exact-time', type=float, default=30)
    parser.add_argument('--iterations', type=int, default=5000)
    parser.add_argument('--timeout-overhead', type=float, default=30)
    parser.add_argument('--economic-config', default='config/cost/synthetic/single_batch_synthetic.json')
    parser.add_argument('--output')
    args = parser.parse_args(argv)
    if any(not math.isfinite(x) or x <= 0 for x in (args.time, args.exact_time, args.timeout_overhead)):
        parser.error('Budgets and overhead must be finite and positive')
    if args.iterations < 0 or any(s < 0 or s > 2_000_000_000 for s in args.seeds):
        parser.error('Invalid iterations or seed')
    cfg, fleet = load_routing_case(args.case)
    if len(MultiBatchPolicy.from_config(fleet['operating_policy']).batches) != 1:
        raise ValueError('Four-model comparison requires B=1')
    parameters = load_json(args.economic_config)
    source = resolve_project_path(args.case).resolve()
    paths = {source} | {resolve_project_path(cfg[key]).resolve() for key in INPUT_KEYS if isinstance(cfg.get(key), str)}
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    variants = []
    for model in range(1, 5):
        config = deepcopy(cfg)
        config.update(model=model, alns_iter=args.iterations, save_plots=False)
        variants.append(dict(config=config, _source_hashes=hashes, model=model))
    out = resolve_project_path(args.output) if args.output else ROOT/'output/experiments/single_batch_comparison_v1'/datetime.now().strftime('%Y%m%dT%H%M%S_%f')
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Refusing to overwrite nonempty output directory: {out}')
    out.mkdir(parents=True, exist_ok=True)
    inputs = freeze_inputs(variants, out)
    sources = source_snapshot(out)
    write_json(out/'economic_parameters.json', parameters)
    methods = ([('exact', args.seeds[0], args.exact_time)] if args.solver in ('both', 'exact') else [])
    if args.solver in ('both', 'alns'):
        methods += [('alns', seed, args.time) for seed in dict.fromkeys(args.seeds)]
    jobs, problems = [], {}
    for variant in variants:
        model = variant['model']
        case_path = out/f'case_M{model}.json'
        write_json(case_path, variant['config'])
        config, model_fleet = load_routing_case(case_path)
        problems[model] = load_instance(config, model_fleet)
        for solver, seed, budget in methods:
            jobs.append(dict(run=f'M{model}_{solver}_seed{seed}', model=model, solver=solver,
                             seed=seed, budget=budget, case_path=str(case_path)))
    write_json(out/'manifest.json', dict(case=str(source), jobs=jobs, input_sha256=inputs,
        source_sha256=sources, economic_parameters=parameters, timeout_overhead=args.timeout_overhead,
        parameter_sha256=hashlib.sha256((out/'economic_parameters.json').read_bytes()).hexdigest(),
        evaluation_scope='per_solver_and_seed; no cross-run cherry picking',
        stage='implementation_validation_and_conditional_economics'))
    rows, results = [], {}
    for job in jobs:
        row = run_worker(job, out, args.timeout_overhead)
        rows.append(row)
        path = out/job['run']/'result.json'
        if row['status'] not in ('ERROR', 'OUTER_TIMEOUT') and path.exists():
            result = json.loads(path.read_text(encoding='utf-8'))
        else:
            result = empty_result(problems[job['model']], job['solver'])
            result['info'].update(status=row['status'], error=row['error'])
        results[job['model'], job['solver'], job['seed']] = result
        with (out/'runs.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"{job['run']}: {row['status']}; valid={row['valid']}; distance={row['objective']}", flush=True)
    comparisons = {}
    for solver, seed, _ in methods:
        key = f'{solver}_seed{seed}'
        compared = compare_single_batch(problems, {m: results[m, solver, seed] for m in problems}, parameters)
        comparisons[key] = compared
        write_json(out/f'comparison_{key}.json', compared)
    report(comparisons, rows, out, parameters)
    print(f'Report: {out / "report.md"}')
    return dict(output_dir=str(out), runs=rows, comparisons=comparisons)


if __name__ == '__main__':
    main()
