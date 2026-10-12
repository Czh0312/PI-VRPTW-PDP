"""M8 adaptive search; no exact solver imports."""
from models.common.cycle.scope import validate_problem
from models.routing.model_8.alns.calendar_search import solve_counts


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42, initial_result=None):
    validate_problem(problem, 8)
    return solve_counts(problem, method='alns', budget=max_time, seed=seed,
                        max_iterations=max_iterations, initial_result=initial_result)
