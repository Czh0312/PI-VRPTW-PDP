"""Current M3: fixed-assignment container cycles, including B=1."""
MODEL_NUMBER = 3
SUPPORTS_EXACT = True

def solve_exact(problem, **kwargs):
    from .multibatch.exact_solver import solve_exact as solve
    problem.operating_policy.require_model(MODEL_NUMBER)
    if not getattr(problem.operating_policy, "multi_batch", False):
        raise ValueError("Current M3 requires multi_batch_cutoff_v1; historical M3 is in baselines")
    return solve(problem, **kwargs)
