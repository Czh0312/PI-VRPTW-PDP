"""MILP construction entry for this numbered model."""
from ..model import validate_problem


def build_subproblem(problem, assignment, **kwargs):
    """Build one assignment MILP; global exact search must cover all assignments."""
    validate_problem(problem)
    from models.routing.common.cycle.assignment import assigned_problem
    from models.routing.solvers.exact.formulation import build_model
    return build_model(assigned_problem(problem, assignment), **kwargs)
