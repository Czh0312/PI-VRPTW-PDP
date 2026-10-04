"""M2: 自由选仓、纯两级配送（一次性包装对照）. Solver implementations live in solvers; shared rules in common.cycle."""
from models.routing.common.cycle.scope import require_scope

MODEL_NUMBER = 2
BATCH_MODE = 'single_batch'
PACKAGING = 'single_use'
ASSIGNMENT = 'free'


def validate_problem(problem):
    require_scope(problem, MODEL_NUMBER, BATCH_MODE)


def build_subproblem(problem, assignment, **kwargs):
    """Compatibility entry; MILP construction lives in exact/model.py."""
    from .exact.model import build_subproblem as build
    return build(problem, assignment, **kwargs)
