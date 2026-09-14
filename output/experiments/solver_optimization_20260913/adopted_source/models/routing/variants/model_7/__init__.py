# models/routing/variants/model_7/__init__.py
"""Model 7: free assignment plus asynchronous homogeneous-box relay."""

MODEL_NUMBER = 7
SUPPORTS_EXACT = True


def solve_exact(*args, **kwargs):
    """Load and run the globally exact assignment-partition solver lazily."""
    from .exact_solver import solve_exact as _solve_exact

    return _solve_exact(*args, **kwargs)


__all__ = ["MODEL_NUMBER", "SUPPORTS_EXACT", "solve_exact"]
