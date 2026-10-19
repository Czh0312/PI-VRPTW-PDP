"""Complete direct MILP construction for M2."""
from models.common.cycle.scope import validate_problem


def build_model(problem, **kwargs):
    validate_problem(problem, 2)
    from models.routing.model_2.exact.formulation import build_model as build_local
    return build_local(problem, **kwargs)


build_joint_model = build_model
