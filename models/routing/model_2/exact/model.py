"""MILP construction entry for this numbered model."""
from models.common.cycle.scope import validate_problem


def build_joint_model(problem, **kwargs):
    """Build the full joint assignment/routing MILP for this numbered model."""
    validate_problem(problem, 2)
    from models.routing.model_2.exact.formulation import build_model
    return build_model(problem, **kwargs)


def build_subproblem(problem, assignment, **kwargs):
    """Build one assignment MILP; global exact search must cover all assignments."""
    validate_problem(problem, 2)
    from models.common.cycle.assignment import assigned_problem
    from models.routing.model_2.exact.formulation import build_model
    return build_model(assigned_problem(problem, assignment), **kwargs)
