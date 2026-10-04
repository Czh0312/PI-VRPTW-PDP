"""M3: 固定分仓、单批次容器循环. Solver implementations live in solvers; shared rules in common.cycle."""
from models.routing.common.cycle.scope import require_scope

MODEL_NUMBER = 3
BATCH_MODE = 'single_batch'
PACKAGING = 'reusable'
ASSIGNMENT = 'fixed'


def validate_problem(problem):
    require_scope(problem, MODEL_NUMBER, BATCH_MODE)


def build_model(problem, **kwargs):
    """Build this model's complete fixed-assignment, vehicle-indexed MILP."""
    validate_problem(problem)
    from models.routing.solvers.exact.formulation import build_model as build_shared
    return build_shared(problem, **kwargs)
