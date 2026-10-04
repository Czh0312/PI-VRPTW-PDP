"""M5: 固定分仓、多批次有限容器循环. Shared constraints live in common.cycle."""
from models.routing.common.cycle.scope import require_scope

MODEL_NUMBER = 5
BATCH_MODE = 'multi_batch'
PACKAGING = 'reusable'
ASSIGNMENT = 'fixed'


def validate_problem(problem):
    require_scope(problem, MODEL_NUMBER, BATCH_MODE)


def build_model(problem, **kwargs):
    """Build this model's complete fixed-assignment, vehicle-indexed MILP."""
    validate_problem(problem)
    from models.routing.common.cycle.model import build_model as build_shared
    return build_shared(problem, **kwargs)
