"""MILP construction entry for this numbered model."""
from ..model import validate_problem


def build_model(problem, **kwargs):
    """Build this model's complete fixed-assignment, vehicle-indexed MILP."""
    validate_problem(problem)
    from models.routing.solvers.exact.formulation import build_model as build_shared
    return build_shared(problem, **kwargs)
