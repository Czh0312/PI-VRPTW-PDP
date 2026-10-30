"""M7 adaptive search; no exact solver imports."""
from models.common.cycle.scope import validate_problem
from models.routing.model_7.alns.k_search.scheduler import solve_counts as aggregate


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42, initial_result=None):
    validate_problem(problem, 7)
    if problem.optimize_loading:
        raise ValueError('The M7 optimized-loading baseline currently supports exact solving only')
    return solve_counts(
        problem,
        method="alns",
        budget=max_time,
        seed=seed,
        max_iterations=max_iterations,
        initial_result=initial_result,
    )


def solve_counts(problem, **kwargs):
    from models.routing.model_7.alns.search import solve_alns as solve
    return aggregate(problem, solve_candidate=solve, **kwargs)
