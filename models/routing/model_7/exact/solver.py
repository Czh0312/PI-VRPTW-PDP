"""M7 MILP solves all configured K with a shared total time budget."""
from models.common.cycle.scope import validate_problem
from models.routing.model_7.exact.search.batch_search import solve_counts as aggregate


def solve_exact(problem, time_limit=60, seed=42, verbose=False, initial_result=None, budget_strategy='adaptive'):
    validate_problem(problem, 7)
    return solve_counts(problem, method='exact', budget=time_limit, seed=seed,
                        verbose=verbose, initial_result=initial_result,
                        budget_strategy=budget_strategy, analytical_bounds=True)


def solve_counts(problem, **kwargs):
    from models.routing.model_7.exact.search.fixed_count import solve_exact as solve
    return aggregate(problem, solve_candidate=solve, **kwargs)
