"""M2: 自由选仓、纯两级配送（一次性包装对照）. Shared constraints live in common.cycle."""
from models.routing.common.cycle.scope import require_scope

MODEL_NUMBER = 2
BATCH_MODE = 'single_batch'
PACKAGING = 'single_use'
ASSIGNMENT = 'free'


def validate_problem(problem):
    require_scope(problem, MODEL_NUMBER, BATCH_MODE)


def build_subproblem(problem, assignment, **kwargs):
    """Build one assignment MILP; global exact search must cover all assignments."""
    validate_problem(problem)
    from models.routing.common.cycle.assignment import assigned_problem
    from models.routing.common.cycle.model import build_model
    return build_model(assigned_problem(problem, assignment), **kwargs)
