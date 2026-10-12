"""M7 MILP solves all configured K with a shared total time budget."""
from models.common.cycle.scope import validate_problem
from models.routing.model_7.exact.calendar_search import solve_counts


def solve_exact(problem, time_limit=60, seed=42, verbose=False):
    validate_problem(problem, 7)
    return solve_counts(problem, method='exact', budget=time_limit, seed=seed,
                        verbose=verbose)
