"""Complete direct MILP construction for M3."""
from models.common.cycle.scope import validate_problem


def build_model(problem, **kwargs):
    validate_problem(problem, 3)
    from models.routing.model_3.exact.formulation import build_model as build_local
    return build_local(problem, **kwargs)
