"""Model 1 public interface: fixed-assignment pure delivery."""

MODEL_NUMBER = 1
SUPPORTS_EXACT = True


def solve_exact(*args, **kwargs):
    """Load and run the exact solver lazily."""
    from archive.routing_baselines.variants.model_1.exact_solver import solve_exact as _solve_exact

    return _solve_exact(*args, **kwargs)


__all__ = ["MODEL_NUMBER", "SUPPORTS_EXACT", "solve_exact"]
