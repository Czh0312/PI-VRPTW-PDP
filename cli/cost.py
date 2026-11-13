"""Price an audited current cycle; use compare-single-batch for lifecycle pairs."""
import argparse
from tools.configuration.loader import load_routing_case
from .route import DEFAULT_CASE

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default=str(DEFAULT_CASE))
    parser.add_argument("--model", type=int, choices=range(1, 6))
    parser.add_argument("--route-result", required=True)
    parser.add_argument("--economic-config")
    work_window = parser.add_mutually_exclusive_group()
    work_window.add_argument('--no-work-window', dest='no_work_window', action='store_true', default=None)
    work_window.add_argument('--fixed-work-window', dest='no_work_window', action='store_false', default=None)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    config, fleet = load_routing_case(args.case)
    if args.model is not None:
        config['model'] = args.model
    if config['model'] not in range(1, 6):
        raise ValueError('The cost command accepts the current cycle models M1–M5')
    if (fleet.get("operating_policy") or {}).get("policy_version") != "multi_batch_cutoff_v1":
        raise ValueError("Only audited multi_batch_cutoff_v1 cycles can be priced; old comparisons were retired")
    if args.no_work_window is not None:
        if config['model'] not in (3, 4, 5):
            raise ValueError('Work window flags here apply to M3/M4/M5')
        config['no_work_window'] = args.no_work_window
    from .multibatch_cost import run_cost
    return run_cost(config, fleet, args)
