"""Price a previously saved, re-audited multibatch route."""
from datetime import datetime
import json
from configuration.loader import PROJECT_ROOT, load_json, resolve_project_path
from models.routing.variants.model_3.multibatch import load_instance
from models.cost.multibatch import evaluate_cycle_cost


def run_cost(config, fleet, args):
    if not args.route_result or not (args.economic_config or config.get('cost_config')):
        raise ValueError('Multibatch cost requires --route-result and an explicit --economic-config (or cost_config)')
    if any(getattr(args, key) is not None for key in ('data', 'time', 'extension_time', 'seed', 'fleet_config',
                                                    'distance_matrix', 'duration_matrix', 'optimized_model')):
        raise ValueError('Multibatch cost replays an existing case/result; route overrides are not supported')
    problem = load_instance(config, fleet)
    source = resolve_project_path(args.route_result)
    result = json.loads(source.read_text(encoding='utf-8'))
    parameters = load_json(args.economic_config or config['cost_config'])
    cost = evaluate_cycle_cost(problem, result, parameters)
    out = resolve_project_path(args.output) if args.output else PROJECT_ROOT/'output/economic_analysis_multibatch'/datetime.now().strftime('%Y%m%dT%H%M%S_%f')
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Refusing to overwrite nonempty output directory: {out}')
    out.mkdir(parents=True, exist_ok=True)
    (out/'cost.json').write_text(json.dumps(dict(route_result=str(source), parameters=parameters, cost=cost),
                                          ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(f"Cycle operating cost: {cost['period_operating_cost']:.6f} {cost['currency']}; initial pool investment: {cost['initial_pool_investment']:.6f}. {out}")
    return cost
