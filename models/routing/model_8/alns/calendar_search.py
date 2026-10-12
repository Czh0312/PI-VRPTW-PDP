"""M8 alns batch-count search; model-local implementation."""
from models.routing.model_8.alns.batch_search import solve_counts as aggregate


def solve_counts(problem, **kwargs):
    from models.routing.model_8.alns.search import solve_variable_count as solve
    return aggregate(problem, solve_candidate=solve, **kwargs)
