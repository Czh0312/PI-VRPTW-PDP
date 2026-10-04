"""Unified command dispatcher.

With no subcommand, run the current B=1 example. Compare runs the fixed-batch
study runner, not a completed M1-M4 comparison.
"""

from __future__ import annotations

import sys

from . import cost, route


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "cost":
        return cost.main(args[1:])
    if args and args[0] == "route":
        return route.main(args[1:])
    if args and args[0] == "compare":
        from experiments.runs.run_multibatch_cycle import main as compare_main

        return compare_main(args[1:])
    return route.main(args)


if __name__ == "__main__":
    main()
