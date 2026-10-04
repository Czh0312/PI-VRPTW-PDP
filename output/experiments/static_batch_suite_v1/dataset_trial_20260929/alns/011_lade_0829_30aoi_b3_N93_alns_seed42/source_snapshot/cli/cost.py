"""Price an audited current cycle. Comparative economics is not yet implemented."""
import argparse
from configuration.loader import load_routing_case
from .route import DEFAULT_CASE

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default=str(DEFAULT_CASE))
    parser.add_argument("--route-result", required=True)
    parser.add_argument("--economic-config")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    config, fleet = load_routing_case(args.case)
    if (fleet.get("operating_policy") or {}).get("policy_version") != "multi_batch_cutoff_v1":
        raise ValueError("Only audited multi_batch_cutoff_v1 cycles can be priced; old comparisons were retired")
    from .multibatch_cost import run_cost
    return run_cost(config, fleet, args)
