"""Dispatch current cycle solvers; historical algorithms require explicit opt-in."""
from importlib import import_module
from .catalog import require_model_number
from .common.operating_policy import policy_for


def _load_solver(model, method):
    module = (f"models.routing.baselines.variants.model_{model}" if method == "exact"
              else f"models.routing.baselines.alns.model_{model}")
    return getattr(import_module(module), "solve_exact" if method == "exact" else "solve_alns")


def solve_routing(*, model, method, problem, e1_tasks=None, e2_tasks_by_sat=None,
                  alns_iter=5000, alns_time=120, seed=42, TFp=9999.0,
                  exact_time=None, exact_backend=None, allow_baseline=False):
    model = require_model_number(int(model))
    policy = policy_for(problem)
    policy.require_model(model)
    method = str(method).lower()
    if method not in {"exact", "alns"}:
        raise ValueError(f"Expected exact or alns, got {method}")
    if getattr(policy, "multi_batch", False):
        if problem.model_number != model:
            raise ValueError("Requested model does not match the loaded instance")
        if e1_tasks or e2_tasks_by_sat:
            raise ValueError("Multibatch quantities must be derived from return events, not static tasks")
        if exact_backend not in (None, "multibatch_indexed"):
            raise ValueError("multi_batch_cutoff_v1 requires multibatch_indexed, not a legacy backend")
        if method == "exact":
            if problem.free_assignment:
                from .variants.single_batch.assignment import solve_exact
            else:
                from .variants.model_3.multibatch.exact_solver import solve_exact
            result = solve_exact(problem, time_limit=exact_time if exact_time is not None else 60, seed=seed)
        else:
            if problem.free_assignment:
                from .variants.single_batch.assignment import solve_alns
            else:
                from .variants.model_3.multibatch.alns import solve_alns
            result = solve_alns(problem, max_time=alns_time, max_iterations=alns_iter, seed=seed)
        if len(policy.batches) == 1 and result.get('audit', {}).get('valid'):
            from .variants.single_batch.timing import normalize_schedule
            result = normalize_schedule(problem, result)
        return result
    if not allow_baseline:
        raise ValueError("Historical policy: use route --baseline / allow_baseline=True explicitly. "
                         "Current M1/M2/M4 require the unified single-batch policy.")
    solver = _load_solver(model, method)
    if method == "exact":
        kwargs = dict(problem=problem, e1_tasks=e1_tasks, e2_tasks_by_sat=e2_tasks_by_sat,
                      vehicles_per_satellite=problem.fleet.e2_per_sat_count, verbose=True,
                      return_info=True, return_incumbent_on_time_limit=True)
        if exact_time is not None:
            kwargs["time_limit"] = exact_time
        if exact_backend is not None:
            kwargs["backend"] = exact_backend
        if model in (3, 4):
            kwargs["TFp"] = TFp
        return solver(**kwargs)
    kwargs = dict(problem=problem, vehicles_per_satellite=problem.fleet.e2_per_sat_count,
                  max_iterations=alns_iter, max_time=alns_time, seed=seed,
                  alns_config_overrides=dict(cooling_schedule="time",
                      stagnation_time=max(0.05, alns_time * 0.30), reheat_fraction=0.5,
                      max_restarts=2, intensification_time=max(0.02, alns_time * 0.08)))
    if model in (1, 3):
        kwargs.update(e1_tasks=e1_tasks, e2_tasks_by_sat=e2_tasks_by_sat, strong_initial_solution=True)
    if model in (3, 4):
        kwargs["TFp"] = TFp
    if policy.single_day:
        kwargs["return_info"] = True
    return solver(**kwargs)
