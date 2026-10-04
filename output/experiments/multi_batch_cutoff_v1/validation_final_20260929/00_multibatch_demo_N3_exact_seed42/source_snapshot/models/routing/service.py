"""Stable application service for dispatching routing solvers.

Solver modules are imported lazily so ALNS remains usable on machines where
the optional Gurobi dependency is not installed.
"""

from importlib import import_module

from .catalog import require_model_number
from .common.operating_policy import policy_for


_EXACT_SOLVERS = {
    number: (f"models.routing.variants.model_{number}", "solve_exact")
    for number in range(1, 9)
}

_ALNS_SOLVERS = {
    **{
        number: (f"models.routing.alns.variants.model_{number}", "solve_alns")
        for number in range(1, 7)
    },
    7: ("models.routing.alns.variants.model_7", "solve_alns"),
    8: ("models.routing.alns.variants.model_8", "solve_alns"),
}


def _load_solver(model: int, method: str):
    registry = _EXACT_SOLVERS if method == "exact" else _ALNS_SOLVERS
    if model not in registry:
        raise ValueError(f"Model {model} 当前不支持 {method.upper()} 求解")
    module_name, function_name = registry[model]
    return getattr(import_module(module_name), function_name)


def solve_routing(
    *,
    model,
    method,
    problem,
    e1_tasks=None,
    e2_tasks_by_sat=None,
    alns_iter=5000,
    alns_time=120,
    seed=42,
    TFp=9999.0,
    stage1_time_ratio=None,
    transshipment_min_net_saving=0.01,
    transshipment_max_total=None,
    transshipment_max_per_satellite=None,
    exact_time=None,
    exact_backend=None,
):
    """Run one canonical routing model through its supported solver."""
    model = require_model_number(int(model))
    policy_for(problem).require_model(model)
    if model == 8 and stage1_time_ratio is not None:
        raise ValueError('M8 已改为联合优化，不再接受 stage1_time_ratio')
    method = str(method).lower()
    if method not in {"exact", "alns"}:
        raise ValueError(f"求解方法必须为 exact 或 alns，收到: {method}")

    if getattr(policy_for(problem), 'multi_batch', False):
        if e1_tasks or e2_tasks_by_sat:
            raise ValueError('Multibatch quantities must be derived from return events, not static tasks')
        if exact_backend not in (None, 'multibatch_indexed'):
            raise ValueError('multi_batch_cutoff_v1 requires multibatch_indexed, not a legacy backend')
        if method == 'exact':
            from .variants.model_3.multibatch.exact_solver import solve_exact
            return solve_exact(problem, time_limit=exact_time if exact_time is not None else 60, seed=seed)
        from .variants.model_3.multibatch.alns import solve_alns
        return solve_alns(problem, max_time=alns_time, max_iterations=alns_iter, seed=seed)

    solver = _load_solver(model, method)
    if method == "exact":
        kwargs = {
            "problem": problem,
            "e1_tasks": e1_tasks,
            "e2_tasks_by_sat": e2_tasks_by_sat,
            "vehicles_per_satellite": problem.fleet.e2_per_sat_count,
            "verbose": True,
            # The application layer must be able to distinguish a certified
            # optimum from a feasible incumbent or a run with no solution.
            "return_info": True,
            "return_incumbent_on_time_limit": True,
        }
        if exact_time is not None:
            kwargs["time_limit"] = exact_time
        if exact_backend is not None:
            if model != 3:
                raise ValueError("exact_backend selection is currently exposed only for Model 3")
            kwargs["backend"] = exact_backend
        if model in (3, 4, 6, 7, 8):
            kwargs["TFp"] = TFp
        return solver(**kwargs)

    adaptive_overrides = {
        "cooling_schedule": "time",
        "stagnation_time": max(0.05, alns_time * 0.30),
        "reheat_fraction": 0.5,
        "max_restarts": 2,
        "intensification_time": max(0.02, alns_time * 0.08),
    }
    kwargs = {
        "problem": problem,
        "vehicles_per_satellite": problem.fleet.e2_per_sat_count,
        "max_iterations": alns_iter,
        "max_time": alns_time,
        "seed": seed,
    }
    if model in (1, 2, 3, 4, 6, 7):
        kwargs["alns_config_overrides"] = adaptive_overrides
    if model not in (2, 4, 7):
        kwargs["e1_tasks"] = e1_tasks
        kwargs["e2_tasks_by_sat"] = e2_tasks_by_sat
    if model in (1, 3, 6):
        kwargs["strong_initial_solution"] = True
    if model in (3, 4, 6, 7, 8):
        kwargs["TFp"] = TFp
    if model in (5, 6, 7):
        kwargs.update(
            transshipment_min_net_saving=transshipment_min_net_saving,
            transshipment_max_total=transshipment_max_total,
            transshipment_max_per_satellite=transshipment_max_per_satellite,
        )
    if model in (6, 7):
        kwargs["stage1_time_ratio"] = stage1_time_ratio
    if model == 8:
        kwargs.update(return_info=True)
    if policy_for(problem).single_day:
        kwargs["return_info"] = True
    return solver(**kwargs)
