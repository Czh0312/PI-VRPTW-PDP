"""Stable application service for dispatching routing solvers.

Solver modules are imported lazily so ALNS remains usable on machines where
the optional Gurobi dependency is not installed.
"""

from importlib import import_module

from .catalog import require_model_number


_EXACT_SOLVERS = {
    number: (f"models.routing.variants.model_{number}", "solve_exact")
    for number in range(1, 8)
}

_ALNS_SOLVERS = {
    **{
        number: (f"models.routing.alns.variants.model_{number}", "solve_alns")
        for number in range(1, 7)
    },
    7: ("models.routing.alns.variants.model_7", "solve_alns"),
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
    e1_tasks,
    e2_tasks_by_sat,
    alns_iter=5000,
    alns_time=120,
    seed=42,
    TFp=9999.0,
    stage1_time_ratio=None,
    transshipment_min_net_saving=0.01,
    transshipment_max_total=None,
    transshipment_max_per_satellite=None,
    exact_time=None,
):
    """Run one canonical routing model through its supported solver."""
    model = require_model_number(int(model))
    method = str(method).lower()
    if method not in {"exact", "alns"}:
        raise ValueError(f"求解方法必须为 exact 或 alns，收到: {method}")

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
        if model in (3, 4, 6, 7):
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
    if model in (3, 4, 6, 7):
        kwargs["TFp"] = TFp
    if model in (5, 6, 7):
        kwargs.update(
            transshipment_min_net_saving=transshipment_min_net_saving,
            transshipment_max_total=transshipment_max_total,
            transshipment_max_per_satellite=transshipment_max_per_satellite,
        )
    if model in (6, 7):
        kwargs["stage1_time_ratio"] = stage1_time_ratio
    return solver(**kwargs)
