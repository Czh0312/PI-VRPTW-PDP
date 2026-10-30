"""Complete direct MILP construction for M1."""
from models.common.cycle.scope import validate_problem


def build_model(problem, **kwargs):
    validate_problem(problem, 1)
    from models.routing.model_1.exact.formulation import build_model as build_local
    return build_local(problem, **kwargs)
