"""Price a previously saved, re-audited multibatch route."""
from datetime import datetime
import json
from tools.configuration.loader import PROJECT_ROOT, load_json, resolve_project_path
from models.common.cycle import load_instance
from models.common.cycle.cost_replay import evaluate_comparison_cost, single_batch_period_profile


def run_cost(config, fleet, args):
    economic = args.economic_config or config.get('economic_config') or config.get('cost_config')
    if not args.route_result or not economic:
        raise ValueError('Multibatch cost requires --route-result and an explicit economic configuration')
    if any(getattr(args, key, None) is not None for key in ('data', 'time', 'extension_time', 'seed', 'fleet_config',
                                                    'distance_matrix', 'duration_matrix', 'optimized_model')):
        raise ValueError('Multibatch cost replays an existing case/result; route overrides are not supported')
    source = resolve_project_path(args.route_result)
    result = json.loads(source.read_text(encoding='utf-8'))
    if config['model'] in (3, 4) and getattr(args, 'no_work_window', None) is None:
        batches = result.get('batches') or []
        if len(batches) == 1:
            saved_policy = result.get('operating_policy')
            no_work_window = (saved_policy.get('repeat_interval') is not None if isinstance(saved_policy, dict)
                              else batches[0].get('cutoff', 0) is None)
            config = {**config, 'no_work_window': no_work_window}
    if config['model'] == 5 and getattr(args, 'no_work_window', None) is None:
        config = {**config, 'no_work_window': result.get('calendar_config', {}).get('no_work_window', False)}
    if not config.get('no_work_window') and config.get('legacy_operating_policy'):
        config = {**config, 'operating_policy': config['legacy_operating_policy']}
        fleet = {**fleet, 'operating_policy': load_json(config['operating_policy'])}
    replay_distance = config['model'] in (1, 2, 3, 4) and result.get('objective_mode', 'distance') == 'distance'
    problem = load_instance(config, fleet, replay_distance=replay_distance)
    parameters = load_json(economic) if isinstance(economic, str) else economic
    if replay_distance and 'pool_lifetime_cycles' in parameters and 'container_pool_cost_per_period' not in parameters:
        parameters = single_batch_period_profile(parameters)
    if getattr(problem, 'joint_batch', False) or 'container_pool_cost_per_period' in parameters:
        cost = evaluate_comparison_cost(problem, result, parameters)
    else:
        from archive.lifecycle_cost.multibatch import evaluate_cycle_cost
        cost = evaluate_cycle_cost(problem, result, parameters)
    out = (resolve_project_path(args.output) if args.output else
           PROJECT_ROOT/'output/current/cost'/datetime.now().strftime('%Y%m%dT%H%M%S_%f'))
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Refusing to overwrite nonempty output directory: {out}')
    out.mkdir(parents=True, exist_ok=True)
    (out/'cost.json').write_text(json.dumps(dict(route_result=str(source), parameters=parameters, cost=cost),
                                          ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    total = cost['total'] if 'total' in cost else cost['period_operating_cost']
    print(f"Cycle cost: {total:.6f} {cost['currency']}; initial pool investment: {cost['initial_pool_investment']:.6f}. {out}")
    return cost
