"""M8 adaptive search; no exact solver imports."""
from models.common.cycle.scope import validate_problem
from models.routing.model_8.alns.search.batch_search import solve_counts as aggregate


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42, initial_result=None,
               budget_strategy="adaptive"):
    validate_problem(problem, 8)
    return solve_counts(
        problem,
        method="alns",
        budget=max_time,
        seed=seed,
        max_iterations=max_iterations,
        initial_result=initial_result,
        budget_strategy=budget_strategy,
    )


def solve_counts(problem, **kwargs):
    from models.routing.model_8.alns.search.calendar import solve_variable_count as solve
    return aggregate(problem, solve_candidate=solve, **kwargs)
