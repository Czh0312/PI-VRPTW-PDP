"""Current M2: 自由选仓、纯两级配送（一次性包装对照）."""
from .model import MODEL_NUMBER, BATCH_MODE, PACKAGING, ASSIGNMENT

SUPPORTS_EXACT = True
__all__ = ['MODEL_NUMBER', 'BATCH_MODE', 'PACKAGING', 'ASSIGNMENT',
           'SUPPORTS_EXACT', 'solve_exact', 'solve_alns']


def solve_exact(*args, **kwargs):
    """Load only this model's exact entry."""
    from .exact_solver import solve_exact as solve
    return solve(*args, **kwargs)


def solve_alns(*args, **kwargs):
    """Load only this model's ALNS entry."""
    from .alns import solve_alns as solve
    return solve(*args, **kwargs)
