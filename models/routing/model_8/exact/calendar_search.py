"""M8 exact batch-count search; model-local implementation."""
from models.routing.model_8.exact.batch_search import solve_counts as aggregate


def solve_counts(problem, **kwargs):
    from models.routing.model_8.exact.joint_solver import solve_exact as solve
    return aggregate(problem, solve_candidate=solve, **kwargs)
