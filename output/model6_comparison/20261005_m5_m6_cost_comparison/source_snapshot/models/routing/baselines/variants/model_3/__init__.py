"""Model 3 public interface: fixed-assignment delivery and later pickup."""

MODEL_NUMBER = 3
SUPPORTS_EXACT = True


def solve_exact(*args, **kwargs):
    """Load and run the exact solver lazily."""
    from .exact_solver import solve_exact as _solve_exact

    return _solve_exact(*args, **kwargs)


__all__ = ["MODEL_NUMBER", "SUPPORTS_EXACT", "solve_exact"]
