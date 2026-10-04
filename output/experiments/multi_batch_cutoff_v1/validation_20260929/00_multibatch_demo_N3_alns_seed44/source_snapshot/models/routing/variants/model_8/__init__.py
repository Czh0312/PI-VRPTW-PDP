"""M8 v3: joint E1/E2 routing, delivery, pickup and capacity-release handoff."""

MODEL_NUMBER = 8
SUPPORTS_EXACT = True


def solve_exact(*args, **kwargs):
    from .exact_solver import solve_exact as solve
    return solve(*args, **kwargs)
