"""Run the current fixed-batch container model; B=1 is the single-batch case."""
import argparse
import sys
from configuration.loader import PROJECT_ROOT, apply_container_pool_size, load_json, load_routing_case
from configuration.container_pools import apply_pool_config

DEFAULT_CASE = PROJECT_ROOT / "config/cases/single_batch_demo.json"

def load_config(case_path=DEFAULT_CASE, **kwargs):
    return load_routing_case(case_path, **kwargs)

def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--baseline" in arguments:
        from .baseline_route import main as baseline_main
        return baseline_main([arg for arg in arguments if arg != "--baseline"])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default=str(DEFAULT_CASE))
    parser.add_argument("--model", type=int, choices=range(1, 6), help="M1/M2 纯配送；M3/M4 单批循环；M5 多批循环")
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
    parser.add_argument("--exact-backend", choices=("multibatch_indexed",))
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
                          ("data", "data"), ("orders", "orders"), ("exact_backend", "exact_backend")):
        value = getattr(args, argument)
        if value is not None:
            config[key] = value
    if args.fleet_config:
        fleet = {**load_json(args.fleet_config), "operating_policy": fleet["operating_policy"]}
        config["routing_config"] = args.fleet_config
    if args.container_pool_size is not None:
        config, fleet = apply_container_pool_size(config, fleet, args.container_pool_size)
    else:
        config, fleet = apply_pool_config(config, fleet, args.pool_config)
    from .multibatch import run_case
    return run_case(config, fleet, case_path=args.case, output=args.output)
