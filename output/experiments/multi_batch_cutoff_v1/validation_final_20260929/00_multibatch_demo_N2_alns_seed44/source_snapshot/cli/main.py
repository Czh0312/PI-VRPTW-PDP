"""Unified command dispatcher.

With no subcommand, the historical behaviour is preserved and a routing case
is solved. Use ``cost`` for economic analysis and ``compare`` for the current
Zhou ALNS scaling experiment.
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
        from experiments.runs.run_model12346_alns_scaling import main as compare_main

        return compare_main(args[1:])
    return route.main(args)


if __name__ == "__main__":
    main()
