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
from tools.configuration.benchmark import DEFAULT_CASE_ID
from tools.configuration.loader import config_sources, load_routing_case, load_json, resolve_project_path, routing_case_source
from experiments.runs.run_multibatch_cycle import (
    INPUT_KEYS, freeze_inputs, source_snapshot, run_worker, write_json)
from experiments.analysis.single_batch_cost import compare_single_batch, incidental_cost_sensitivity
from models.common.cycle import load_instance, MultiBatchPolicy
from models.common.cycle.cost_replay import single_batch_period_profile
from models.common.cycle.state import empty_result
from models.common.period_cost import DEFAULT_ECONOMIC_CONFIG


def _loss_limit_text(rate):
    return f'{rate:.2%}' if rate is not None else '无法持平'


def _incidental_report(comparison):
    costs = comparison.get('incidental_costs', {})
    if not costs:
        return []
    rates = next(iter(costs.values()))
    lines = ['', '### 偶然成本情景', '',
             f"补购一只箱计 {rates['replacement_cost_per_box']:g}，每发箱箱次其他偶然支出计 {rates['other_cost_per_use']:g}。",
             '损耗率按基准费用之外的额外补购估计。各情景沿用同一组已审计方案。', '',
             '| 对照 | 每期损耗率上限 |', '| --- | ---: |']
    for pair, cost in costs.items():
        period = _loss_limit_text(cost['period_max_loss_rate']) if cost['packages_per_period'] else '—'
        lines.append(f'| {pair} | {period} |')
    lines += ['', '| 对照 | 损耗率 | 每期偶然成本 | 调整后每期节省 | 每期纸箱持平单价 |',
              '| --- | ---: | ---: | ---: | ---: |']
    for pair, cost in costs.items():
        for scenario in cost['scenarios']:
            threshold = scenario['period_paper_price_threshold']
            threshold = f'{threshold:.6f}' if threshold is not None else '—'
            lines.append(f"| {pair} | {scenario['loss_rate']:.2%} | {scenario['period_incidental_cost']:.6f} | "
                         f"{scenario['period_saving']:.6f} | {threshold} |")
    return lines


def report(comparisons, rows, out, parameters):
    lines = ['# 单批次 M1–M4 对照', '',
             f"费率来源类型为 `{parameters['source_type']}`，成本单位为 `{parameters['currency']}`。", '',
             '| 求解器/种子 | 模型 | 状态 | 审计 | 物理距离 | 成本目标值 | 成本最优已证明 |',
             '| --- | --- | --- | --- | ---: | ---: | --- |']
    for row in rows:
        distance = f"{row['e1_distance'] + row['e2_distance']:.6f}" if row['valid'] else '—'
        objective = f"{row['objective']:.6f}" if row['valid'] else '—'
        lines.append(f"| {row['solver']}/{row['seed']} | M{row['model']} | {row['status']} | "
                     f"{row['valid']} | {distance} | {objective} | {row['proven_optimal']} |")
    for name, comparison in comparisons.items():
        lines += ['', f'## {name}', '',
                  '| 模型 | 每期总成本 | 实体箱池数 | 箱池购置估值 |', '| --- | ---: | ---: | ---: |']
        for model, cost in comparison['costs'].items():
            lines.append(f"| M{model} | {cost['total']:.6f} | {cost['container_pool_size']} | "
                         f"{cost['initial_pool_investment']:.6f} |")
        lines += ['', '| 对照 | 循环方案每期节省 | 每配送一箱节省 | 纸箱单价持平阈值 |',
                  '| --- | ---: | ---: | ---: |']
        for pair, data in comparison['comparisons'].items():
            if data['available']:
                per_delivery = data['period_saving_per_delivery']
                threshold = data['period_single_use_purchase_price_threshold']
                per_delivery_text = f'{per_delivery:.6f}' if per_delivery is not None else '—'
                threshold_text = f'{threshold:.6f}' if threshold is not None else '—'
                lines.append(f"| {pair} | {data['period_saving']:.6f} | {per_delivery_text} | {threshold_text} |")
            else:
                lines.append(f'| {pair} | 缺少成对的审计合格方案 | — | — |')
        if comparison['sensitivity']:
            lines += ['', '敏感性分析沿用同一组已审计路线和时间表。', '',
                      '| 对照 | 一次性包装单价 | 每期节省 |', '| --- | ---: | ---: |']
            for data in comparison['sensitivity']:
                lines.append(f"| M{data['single_use_model']}/M{data['reusable_model']} | "
                             f"{data['single_use_package_price']:g} | {data['period_saving']:.6f} |")
        lines += _incidental_report(comparison)
    lines += ['', '基准每期费用与求解目标同口径。负节省表示循环方案更贵。',
              '费用明细和证明范围见 comparison_*.json。运行状态见 runs.csv。']
    (out/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', default=DEFAULT_CASE_ID)
    parser.add_argument('--solver', choices=('both', 'exact', 'alns'), default='both')
    parser.add_argument('--seeds', nargs='+', type=int, default=[42])
    parser.add_argument('--time', type=float, default=2)
    parser.add_argument('--exact-time', type=float, default=30)
    parser.add_argument('--iterations', type=int, default=5000)
    parser.add_argument('--timeout-overhead', type=float, default=30)
    parser.add_argument('--economic-config', help='Shared pricing profile used for all four solves and their comparison')
    parser.add_argument('--loss-rates', type=float, nargs='+', default=[0.0],
                        help='Incremental replacement rates per issued box (e.g. 0.03 for 3%%)')
    parser.add_argument('--loss-replacement-cost-per-box', type=float,
                        help='Cost of one loss, defaulting to the profile container price')
    parser.add_argument('--other-incidental-cost-per-use', type=float, default=0.0,
                        help='Expected additional operating cost per issued box')
    parser.add_argument('--output')
    args = parser.parse_args(argv)
    if any(not math.isfinite(x) or x <= 0 for x in (args.time, args.exact_time, args.timeout_overhead)):
        parser.error('Budgets and overhead must be finite and positive')
    if args.iterations < 0 or any(s < 0 or s > 2_000_000_000 for s in args.seeds):
        parser.error('Invalid iterations or seed')
    if any(not 0 <= rate <= 1 for rate in args.loss_rates):
        parser.error('Loss rates must be between 0 and 1')
    if any(value is not None and (not math.isfinite(value) or value < 0)
           for value in (args.loss_replacement_cost_per_box, args.other_incidental_cost_per_use)):
        parser.error('Incidental cost rates must be finite and nonnegative')
    cfg, fleet = load_routing_case(args.case, model=3)
    comparison_policy = (load_json(cfg['legacy_operating_policy']) if cfg.get('legacy_operating_policy')
                         else fleet['operating_policy'])
    if len(MultiBatchPolicy.from_config(comparison_policy).batches) != 1:
        raise ValueError('Four-model comparison requires B=1')
    economic = args.economic_config or cfg.get('economic_config') or DEFAULT_ECONOMIC_CONFIG
    cfg['economic_config'] = economic
    parameters = load_json(economic) if isinstance(economic, str) else economic
    source = routing_case_source(args.case, cfg).resolve()
    paths = {source} | {resolve_project_path(cfg[key]).resolve() for key in INPUT_KEYS if isinstance(cfg.get(key), str)}
    paths = {dependency for path in paths for dependency in config_sources(path)}
    if parameters.get('evidence_file'):
        paths.update(config_sources(parameters['evidence_file']))
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    variants = []
    for model in range(1, 5):
        config = deepcopy(cfg)
        # Hold the outer window fixed across all four variants.
        config.update(model=model, alns_iter=args.iterations, save_plots=False, no_work_window=False)
        if config.get('legacy_operating_policy'):
            config['operating_policy'] = config['legacy_operating_policy']
            policy_path = resolve_project_path(config['operating_policy']).resolve()
            hashes[str(policy_path)] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
        variants.append(dict(config=config, _source_hashes=hashes, model=model))
    out = resolve_project_path(args.output) if args.output else ROOT/'output/current/single_batch_comparison'/datetime.now().strftime('%Y%m%dT%H%M%S_%f')
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Refusing to overwrite nonempty output directory: {out}')
    out.mkdir(parents=True, exist_ok=True)
    inputs = freeze_inputs(variants, out)
    sources = source_snapshot(out)
    economic = variants[0]['config']['economic_config']
    parameters = single_batch_period_profile(load_json(economic) if isinstance(economic, str) else economic)
    replacement = args.loss_replacement_cost_per_box
    incidental = dict(loss_rates=args.loss_rates, other_cost_per_use=args.other_incidental_cost_per_use,
                      replacement_cost_per_box=parameters['container_price'] if replacement is None else replacement)
    if parameters.get('evidence_file'):
        evidence = inputs[str(resolve_project_path(parameters['evidence_file']).resolve())]
        parameters = {**parameters, 'evidence_file': str(out/evidence.get('resolved_snapshot', evidence['snapshot']))}
    economic_path = out/'economic_parameters.json'
    write_json(economic_path, parameters)
    methods = ([('exact', args.seeds[0], args.exact_time)] if args.solver in ('both', 'exact') else [])
    if args.solver in ('both', 'alns'):
        methods += [('alns', seed, args.time) for seed in dict.fromkeys(args.seeds)]
    jobs, problems = [], {}
    for variant in variants:
        model = variant['model']
        case_path = out/f'case_M{model}.json'
        variant['config']['economic_config'] = str(economic_path)
        write_json(case_path, variant['config'])
        config, model_fleet = load_routing_case(case_path)
        problems[model] = load_instance(config, model_fleet)
        for solver, seed, budget in methods:
            jobs.append(dict(run=f'M{model}_{solver}_seed{seed}', model=model, solver=solver,
                             seed=seed, budget=budget, case_path=str(case_path)))
    write_json(out/'manifest.json', dict(case=str(source), jobs=jobs, input_sha256=inputs,
        source_sha256=sources, economic_parameters=parameters, timeout_overhead=args.timeout_overhead,
        incidental_cost_parameters=incidental,
        parameter_sha256=hashlib.sha256(economic_path.read_bytes()).hexdigest(),
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
        print(f"{job['run']}: {row['status']}; valid={row['valid']}; cost={row['objective']}", flush=True)
    comparisons = {}
    for solver, seed, _ in methods:
        key = f'{solver}_seed{seed}'
        compared = compare_single_batch(problems, {m: results[m, solver, seed] for m in problems}, parameters)
        compared['incidental_costs'] = {
            name: incidental_cost_sensitivity(pair, packages=problems[pair['single_use_model']].total, **incidental)
            for name, pair in compared['comparisons'].items() if pair['available']
        }
        comparisons[key] = compared
        write_json(out/f'comparison_{key}.json', compared)
    report(comparisons, rows, out, parameters)
    print(f'Report: {out / "report.md"}')
    return dict(output_dir=str(out), runs=rows, comparisons=comparisons)


if __name__ == '__main__':
    main()
