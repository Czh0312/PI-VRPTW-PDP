"""M1: 固定分仓、纯两级配送（一次性包装对照）. Solver implementations live in solvers; shared rules in common.cycle."""
from models.routing.common.cycle.scope import require_scope

MODEL_NUMBER = 1
BATCH_MODE = 'single_batch'
PACKAGING = 'single_use'
ASSIGNMENT = 'fixed'


def validate_problem(problem):
    require_scope(problem, MODEL_NUMBER, BATCH_MODE)


def build_model(problem, **kwargs):
    """Compatibility entry; MILP construction lives in exact/model.py."""
    from .exact.model import build_model as build
    return build(problem, **kwargs)
