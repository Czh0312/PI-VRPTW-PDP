"""Run the current fixed-batch container model; B=1 is the single-batch case."""
import argparse
import sys
from tools.configuration.loader import PROJECT_ROOT, apply_container_pool_size, load_json, load_routing_case
from tools.configuration.container_pools import apply_pool_config

DEFAULT_CASE = PROJECT_ROOT / "config/experiments/examples/single_batch_demo.json"

def load_config(case_path=DEFAULT_CASE, **kwargs):
    return load_routing_case(case_path, **kwargs)

def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--baseline" in arguments:
        from .baseline_route import main as baseline_main
        return baseline_main([arg for arg in arguments if arg != "--baseline"])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default=str(DEFAULT_CASE))
    parser.add_argument("--model", type=int, choices=range(1, 9), help="M1–M5 原模型；M6 统一分界；M7 独立分界；M8 不等长批次")
    parser.add_argument("--economic-config", help="M6–M8 成本参数与证据配置")
    schedules=parser.add_mutually_exclusive_group()
    schedules.add_argument('--uniform-cycle',dest='uniform_cycle',action='store_true',default=None,
                           help='M6 基础版：优化周期批次数，均分周期，各批共用相对分界点')
    schedules.add_argument('--candidate-slots',dest='uniform_cycle',action='store_false',default=None,
                           help='M6 历史候选时段模式，保留输入批次起止')
    counts = parser.add_mutually_exclusive_group()
    counts.add_argument('--batch-count-candidates',type=int,nargs='+',help='显式指定用于诊断的 K 子集')
    counts.add_argument('--all-batch-counts',action='store_true',help='M6–M8 允许全部满足周期及最短阶段约束的正整数 K')
    parser.add_argument('--min-phase-minutes',type=float,help='M6–M8 各阶段最短时长，默认1分钟（场景假设）')
    cutoffs=parser.add_mutually_exclusive_group()
    cutoffs.add_argument('--optimize-cutoffs',dest='optimize_cutoffs',action='store_true',default=None,
                         help='M6 优化两窗截点，保持候选批次起止与顾客窗口不变')
    cutoffs.add_argument('--fixed-cutoffs',dest='optimize_cutoffs',action='store_false',default=None,
                         help='M6 使用原固定截点，作为对照')
    parser.add_argument('--initial-joint-result',help='M6–M8 ALNS 同模型已有可行解 JSON；审计后保留')
    parser.add_argument("--solver", choices=("alns", "exact"))
    parser.add_argument("--exact-time", type=float)
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
                          ("economic_config", "economic_config")):
        value = getattr(args, argument)
        if value is not None:
            config[key] = value
    if args.fleet_config:
        fleet = {**load_json(args.fleet_config), "operating_policy": fleet["operating_policy"]}
        config["routing_config"] = args.fleet_config
    options=dict(config.get('joint_batch_options',{}))
    if args.model in (7, 8):
        from models.routing.common.cycle.calendar import SCHEDULE_MODES
        options['schedule_mode'] = SCHEDULE_MODES[args.model]
        config['joint_batch_options'] = options
    if args.uniform_cycle is not None:
        if config['model']!=6:
            raise ValueError('Schedule optimization flags require M6')
        options={key:value for key,value in options.items() if key in ('pool_min','pool_max')}
        if args.uniform_cycle:
            options['schedule_mode']='uniform_cycle'
        config['joint_batch_options']=options
    if args.all_batch_counts or args.batch_count_candidates is not None or args.min_phase_minutes is not None:
        if config['model'] not in (7, 8) and (config['model']!=6 or options.get('schedule_mode')!='uniform_cycle'):
            raise ValueError('Batch-count and phase-minimum options require M6 --uniform-cycle or M7/M8')
        if args.batch_count_candidates is not None:
            options['batch_count_candidates']=args.batch_count_candidates
        elif args.all_batch_counts:
            options['batch_count_candidates']='all_positive_integers'
        if args.min_phase_minutes is not None:
            options['min_phase_minutes']=args.min_phase_minutes
        config['joint_batch_options']=options
    if args.optimize_cutoffs is not None:
        if config['model']!=6:
            raise ValueError('Cutoff optimization flags require M6')
        if options.get('schedule_mode')=='uniform_cycle':
            raise ValueError('Uniform cycle always optimizes a shared cutoff; individual cutoff flags are incompatible')
        options={**config.get('joint_batch_options',{}),'optimize_cutoffs':args.optimize_cutoffs}
        if not args.optimize_cutoffs:
            options.pop('cutoff_bounds_minutes',None)
        config['joint_batch_options']=options
    if args.initial_joint_result:
        config['initial_joint_result']=args.initial_joint_result
    if args.container_pool_size is not None:
        config, fleet = apply_container_pool_size(config, fleet, args.container_pool_size)
        if config['model'] in (6, 7, 8):
            config['joint_batch_options'] = {**config.get('joint_batch_options', {}),
                                           'pool_min': args.container_pool_size, 'pool_max': args.container_pool_size}
    elif config['model'] not in (6, 7, 8):
        config, fleet = apply_pool_config(config, fleet, args.pool_config)
    from .multibatch import run_case
    return run_case(config, fleet, case_path=args.case, output=args.output)
