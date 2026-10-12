"""M7 alns batch-count search; model-local implementation."""
from models.routing.model_7.alns.batch_search import solve_counts as aggregate


def solve_counts(problem, **kwargs):
    from models.routing.model_7.alns.joint_solver import solve_alns as solve
    return aggregate(problem, solve_candidate=solve, **kwargs)
