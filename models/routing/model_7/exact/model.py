"""One complete M7 cost MILP per configured batch count."""
from models.common.cycle.scope import validate_problem


def build_model(problem, *, batch_count=None, verbose=False, deadline=None):
    validate_problem(problem, 7)
    if batch_count is None:
        if len(problem.batch_count_candidates) != 1:
            raise ValueError('Specify batch_count for a single MILP; solve_exact searches all K')
        batch_count = problem.batch_count_candidates[0]
    from models.routing.model_7.exact.joint_model import build_model as build_joint
    return build_joint(problem.for_count(batch_count), verbose=verbose, deadline=deadline)

