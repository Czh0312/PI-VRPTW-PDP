"""MILP construction entry for this numbered model."""
from models.common.cycle.scope import validate_problem


def build_model(problem, **kwargs):
    """Build this model's complete fixed-assignment, vehicle-indexed MILP."""
    validate_problem(problem, 1)
    from models.routing.model_1.exact.formulations.full import build_model as build_local
    return build_local(problem, **kwargs)
