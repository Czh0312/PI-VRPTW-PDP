"""M6 public entry: validate input and dispatch fixed-K or K scheduling."""
import math

from models.common.cycle.scope import validate_problem

# Compatibility exports; new code imports helpers from their defining modules.
from models.routing.model_6.alns.search import (
    JointEvaluator, _best_construction, _cutoff_events, _polish_cutoff,
    _with_cutoff, _cutoff_move, clone_state,
)


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42, initial_result=None):
    validate_problem(problem, 6)
    if problem.optimize_loading:
        raise ValueError('Optimized loading requires the M7 extended exact entry')
    if (
        isinstance(max_time, bool)
        or not math.isfinite(max_time)
        or max_time <= 0
        or isinstance(max_iterations, bool)
        or not isinstance(max_iterations, int)
        or max_iterations < 0
    ):
        raise ValueError("Invalid ALNS time or iteration budget")
    if getattr(problem, "uniform_cycle", False):
        from models.routing.model_6.alns.k_search.scheduler import solve_uniform

        return solve_uniform(
            problem,
            solve_candidate=solve_alns,
            method="alns",
            budget=max_time,
            max_iterations=max_iterations,
            seed=seed,
            initial_result=initial_result,
        )
    from models.routing.model_6.alns.search import solve_alns as solve_search

    return solve_search(problem, max_time=max_time, max_iterations=max_iterations,
                        seed=seed, initial_result=initial_result)
