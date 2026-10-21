"""Complete direct MILP construction for M4."""
from models.common.cycle.scope import validate_problem


def build_model(problem, **kwargs):
    validate_problem(problem, 4)
    from models.routing.model_4.exact.formulation import build_model as build_local
    return build_local(problem, **kwargs)


build_joint_model = build_model
