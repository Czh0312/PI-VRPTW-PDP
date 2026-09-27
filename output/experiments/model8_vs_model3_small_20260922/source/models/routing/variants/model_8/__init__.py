"""M8 v2: pickup reassignment and capacity-release relay on a delivery backbone."""

MODEL_NUMBER = 8
SUPPORTS_EXACT = True


def solve_exact(*args, **kwargs):
    from .exact_solver import solve_exact as solve
    return solve(*args, **kwargs)
