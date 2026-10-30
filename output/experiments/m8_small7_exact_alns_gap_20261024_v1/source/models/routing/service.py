"""Dispatch current cycle solvers; historical algorithms require explicit opt-in."""
from importlib import import_module
from .catalog import MODEL_SPECS, require_model_number
from models.common.operating_policy import policy_for


def _load_solver(model, method):
    module = (f"archive.routing_baselines.variants.model_{model}" if method == "exact"
              else f"archive.routing_baselines.alns.model_{model}")
    return getattr(import_module(module), "solve_exact" if method == "exact" else "solve_alns")


def solve_routing(*, model, method, problem, e1_tasks=None, e2_tasks_by_sat=None,
                  alns_iter=5000, alns_time=120, seed=42, TFp=9999.0,
                  exact_time=None, exact_backend=None, allow_baseline=False, initial_result=None,
                  exact_strategy=None, milp_options=None, m4_strengthening=None):
    model = require_model_number(int(model))
    policy = policy_for(problem)
    policy.require_model(model)
    method = str(method).lower()
    if method not in {"exact", "alns"}:
        raise ValueError(f"Expected exact or alns, got {method}")
    if m4_strengthening is not None and (model != 4 or method != 'exact' or not getattr(policy, 'multi_batch', False)):
        raise ValueError('M4 strengthening requires the current M4 exact solver')
    if method != 'exact' and (exact_strategy is not None or milp_options is not None):
        raise ValueError('Exact strategy/options require method="exact"')
    if milp_options is not None and exact_strategy not in (None, 'auto', 'milp'):
        raise ValueError('milp_options requires exact_strategy="milp"')
    if initial_result is not None and method == 'exact':
        raise ValueError('Direct Gurobi does not accept an external initial solution')
    if initial_result is not None and model not in (6, 7, 8):
        raise ValueError('Initial joint solution requires M6–M8')
    if getattr(policy, "multi_batch", False):
        if problem.model_number != model:
            raise ValueError("Requested model does not match the loaded instance")
        if e1_tasks or e2_tasks_by_sat:
            raise ValueError("Multibatch quantities must be derived from return events, not static tasks")
        expected_backend = 'joint_batch_indexed' if model in (6, 7, 8) else 'multibatch_indexed'
        if exact_backend not in (None, expected_backend):
            raise ValueError(f'Model {model} requires {expected_backend}; incompatible or legacy backend: {exact_backend}')
        from models.common.cycle.scope import batch_mode_for
        module = import_module(MODEL_SPECS[model].module_path(batch_mode_for(problem), method))
        if method == "exact":
            kwargs = dict(time_limit=exact_time if exact_time is not None else 60, seed=seed)
            if exact_strategy is not None:
                kwargs['strategy'] = exact_strategy
            if milp_options is not None:
                kwargs['milp_options'] = milp_options
            if m4_strengthening is not None:
                kwargs['strengthening'] = m4_strengthening
            return module.solve_exact(problem, **kwargs)
        kwargs=dict(max_time=alns_time,max_iterations=alns_iter,seed=seed)
        if initial_result is not None:
            kwargs['initial_result']=initial_result
        return module.solve_alns(problem, **kwargs)
    if exact_strategy is not None or milp_options is not None:
        raise ValueError('Exact strategies require the current cycle policy, not the historical baseline')
    if model == 5:
        raise ValueError('Current M5 requires multi_batch_cutoff_v1 with B>1; historical M5 was retired')
    if model in (6, 7, 8):
        raise ValueError(f'Current M{model} requires multi_batch_cutoff_v1 and explicit joint batch/cost inputs')
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
