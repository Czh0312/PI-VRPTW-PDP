"""M8 adaptive search; no exact solver imports."""
from models.common.cycle.scope import validate_problem
from models.routing.model_8.alns.k_search.scheduler import solve_counts as aggregate
from models.routing.model_8.alns.parameters import validate_calendar_reconstruction_period


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42, initial_result=None,
               budget_strategy="adaptive", *, calendar_reconstruction_period=1):
    validate_problem(problem, 8)
    return solve_counts(
        problem,
        method="alns",
        budget=max_time,
        seed=seed,
        max_iterations=max_iterations,
        initial_result=initial_result,
        budget_strategy=budget_strategy,
        calendar_reconstruction_period=calendar_reconstruction_period,
    )


def solve_counts(problem, *, calendar_reconstruction_period=1, **kwargs):
    from functools import partial
    from models.routing.model_8.alns.search import solve_variable_count as solve
    period = validate_calendar_reconstruction_period(calendar_reconstruction_period)
    candidate = solve if period == 1 else partial(solve, calendar_reconstruction_period=period)
    result = aggregate(problem, solve_candidate=candidate, **kwargs)
    result['info']['calendar_reconstruction_period'] = period
    return result
