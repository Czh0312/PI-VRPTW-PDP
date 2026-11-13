"""Solve a current two-echelon routing case."""
import argparse
import sys
from tools.configuration.loader import PROJECT_ROOT, apply_container_pool_size, load_json, load_routing_case
from tools.configuration.container_pools import apply_pool_config

DEFAULT_CASE = PROJECT_ROOT / "config/experiments/examples/single_batch_demo.json"
DEMO_CASES = {4: DEFAULT_CASE.with_name('model4_demo.json'),
              5: DEFAULT_CASE.with_name('model5_demo.json')}


def load_config(case_path=DEFAULT_CASE, **kwargs):
    return load_routing_case(case_path, **kwargs)


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    # The no-batch model uses its own solver options.
    probe = argparse.ArgumentParser(add_help=False)
    probe.add_argument('--model', type=int)
    probe.add_argument('--case')
    selected, _ = probe.parse_known_args(arguments)
    case_path = selected.case or DEMO_CASES.get(selected.model, DEFAULT_CASE)
    number = selected.model if selected.model is not None else load_json(case_path).get('model')
    if number == 6:
        from .no_batch import main as no_batch_main
        return no_batch_main(arguments)
    if "--baseline" in arguments:
        from .baseline_route import main as baseline_main
        return baseline_main([arg for arg in arguments if arg != "--baseline"])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default=str(case_path))
    parser.add_argument("--model", type=int, choices=range(1, 7), help="M1–M4 单批次，M5 可变批次，M6 无批次")
    parser.add_argument("--economic-config", help="M3/M5 总成本参数")
    parser.add_argument('--objective-mode', choices=('cost', 'distance'),
                        help='M3 选择 distance 或 cost，M5 使用 cost')
    work_window = parser.add_mutually_exclusive_group()
    work_window.add_argument('--no-work-window', dest='no_work_window', action='store_true', default=None,
                             help='M3/M4/M5 按订单和作业时序排程，次日首单释放前复位')
    work_window.add_argument('--fixed-work-window', dest='no_work_window', action='store_false', default=None,
                             help='M3/M4/M5 复现配置中的固定外层作业窗口')
    parser.add_argument('--pool-min', type=int, help='M5 容器池决策下界')
    parser.add_argument('--pool-max', type=int, help='M5 容器池决策上界')
    counts = parser.add_mutually_exclusive_group()
    counts.add_argument('--batch-count-candidates',type=int,nargs='+',help='显式指定用于诊断的 K 子集')
    counts.add_argument('--all-batch-counts',action='store_true',help='M5 允许全部满足周期及最短阶段约束的正整数 K')
    parser.add_argument('--min-phase-minutes',type=float,help='M5 各阶段最短时长，默认1分钟')
    parser.add_argument('--initial-joint-result',help='M5 ALNS 初解，使用独立审计过的同模型结果 JSON')
    parser.add_argument("--solver", choices=("alns", "exact"))
    parser.add_argument("--exact-time", type=float)
    parser.add_argument("--exact-strategy", choices=("auto", "milp"),
                        help="直接 Gurobi，M5 在同一 MILP 中优化批次数")
    parser.add_argument("--milp-threads", type=int, help="直接MILP基线的线程数，默认1")
    parser.add_argument("--milp-formulation", choices=("expanded", "compact"),
                        help="直接MILP的等价表达，M3/M4默认compact，其余默认expanded")
    parser.add_argument('--m4-strengthening', choices=('none','bounds','capacity','combined','activation','capacity_activation'),
                        help='M4精确MILP的有效约束强化；默认none，其余为显式对照选项')
    parser.add_argument("--time", type=float)
    parser.add_argument("--iterations", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--data")
    parser.add_argument("--orders")
    parser.add_argument("--fleet-config")
    parser.add_argument("--container-pool-size", type=int, help="覆盖周期开始时的清洁容器总池量 N（正整数）")
    parser.add_argument("--pool-config", help="替换默认的统一容器池配置")
    parser.add_argument("--exact-backend", choices=("multibatch_indexed", "joint_batch_indexed"))
    parser.add_argument("--day-end", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--output", help="New or empty result directory")
    parser.add_argument("--baseline", action="store_true", help="显式运行隔离的历史 M1–M4 基线")
    args = parser.parse_args(arguments)
    config, fleet = load_config(args.case, use_pool_config=False)
    if args.day_end is not None:
        raise ValueError("--day-end is single-day only; edit the current calendar/cycle_end explicitly")
    if (fleet.get("operating_policy") or {}).get("policy_version") != "multi_batch_cutoff_v1":
        raise ValueError("Historical case: use --baseline explicitly; current cases require multi_batch_cutoff_v1")
    for argument, key in (("model", "model"), ("solver", "solver"), ("exact_time", "exact_time"),
                          ("time", "alns_time"), ("iterations", "alns_iter"), ("seed", "seed"),
                          ("data", "data"), ("orders", "orders"), ("exact_backend", "exact_backend"),
                          ("exact_strategy", "exact_strategy"),
                          ("economic_config", "economic_config")):
        value = getattr(args, argument)
        if value is not None:
            config[key] = value
    if args.objective_mode is not None:
        if config['model'] not in (3, 5):
            raise ValueError('--objective-mode is only supported for M3 and M5')
        if config['model'] == 5 and args.objective_mode != 'cost':
            raise ValueError('Model 5 uses the total-cost objective')
        config['objective_mode'] = args.objective_mode
    elif args.economic_config and config['model'] in (3, 5):
        config['objective_mode'] = 'cost'
    if args.no_work_window is not None:
        if config['model'] not in (3, 4, 5):
            raise ValueError('Work window flags here apply to M3/M4/M5')
        config['no_work_window'] = args.no_work_window
    elif config['model'] in (3, 4, 5):
        config.setdefault('no_work_window', True)
    else:
        config.pop('no_work_window', None)
    if not config.get('no_work_window') and config.get('legacy_operating_policy'):
        config['operating_policy'] = config['legacy_operating_policy']
        fleet = {**fleet, 'operating_policy': load_json(config['operating_policy'])}
    if args.m4_strengthening is not None:
        if config['model'] != 4 or config.get('solver') != 'exact':
            raise ValueError('--m4-strengthening requires M4 --solver exact')
        config['m4_strengthening'] = args.m4_strengthening
    milp_options = dict(config.get('milp_options') or {})
    if args.milp_threads is not None:
        milp_options['threads'] = args.milp_threads
    if args.milp_formulation is not None:
        milp_options['formulation'] = args.milp_formulation
    if milp_options:
        config['milp_options'] = milp_options
    if args.fleet_config:
        fleet = {**load_json(args.fleet_config), "operating_policy": fleet["operating_policy"]}
        config["routing_config"] = args.fleet_config
    options=dict(config.get('joint_batch_options',{}))
    if args.all_batch_counts or args.batch_count_candidates is not None or args.min_phase_minutes is not None:
        if config['model'] != 5:
            raise ValueError('Batch-count and phase-minimum options require M5')
        if args.batch_count_candidates is not None:
            options['batch_count_candidates']=args.batch_count_candidates
        elif args.all_batch_counts:
            options['batch_count_candidates']='all_positive_integers'
        if args.min_phase_minutes is not None:
            options['min_phase_minutes']=args.min_phase_minutes
        config['joint_batch_options']=options
    if args.initial_joint_result:
        config['initial_joint_result']=args.initial_joint_result
    if args.pool_min is not None or args.pool_max is not None:
        if config['model'] != 5:
            raise ValueError('Pool decision bounds require M5')
        key = 'joint_batch_options'
        bounds = dict(config.get(key, {}))
        for name in ('pool_min', 'pool_max'):
            if getattr(args, name) is not None:
                bounds[name] = getattr(args, name)
        config[key] = bounds
    if args.container_pool_size is not None:
        config, fleet = apply_container_pool_size(config, fleet, args.container_pool_size)
        if config['model'] == 5:
            key = 'joint_batch_options'
            config[key] = {**config.get(key, {}),
                                           'pool_min': args.container_pool_size, 'pool_max': args.container_pool_size}
    elif config['model'] != 5:
        config, fleet = apply_pool_config(config, fleet, args.pool_config)
    elif args.pool_config:
        raise ValueError('Use --pool-min/--pool-max for M5')
    from .multibatch import run_case
    return run_case(config, fleet, case_path=args.case, output=args.output)
