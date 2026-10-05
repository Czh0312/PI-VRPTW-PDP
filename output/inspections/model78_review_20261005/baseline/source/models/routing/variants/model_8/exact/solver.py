"""M8 MILP solves all configured K with a shared total time budget."""
from ..model import validate_problem
from models.routing.variants.calendar_search import solve_counts


def solve_exact(problem, time_limit=60, seed=42, verbose=False):
    validate_problem(problem)
    return solve_counts(problem, method='exact', budget=time_limit, seed=seed,
                        verbose=verbose)
