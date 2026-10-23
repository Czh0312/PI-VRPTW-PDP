"""ALNS entry for M5: 固定分仓、多批次有限容器循环."""
from models.common.cycle.scope import validate_problem, finish_result


def solve_alns(
    problem, max_time=10, max_iterations=5000, seed=42, *, cache_descent_distances=False,
    cache_e2_schedule=False,
    critical_stock_repair=False,
    stock_chain_strategy='stock_first',
):
    validate_problem(problem, 5)
    from models.routing.model_5.alns.search import solve_alns as solve_local

    options = dict(max_time=max_time, max_iterations=max_iterations, seed=seed)
    if cache_descent_distances:
        options["cache_descent_distances"] = True
    if cache_e2_schedule:
        options["cache_e2_schedule"] = True
    if critical_stock_repair:
        options["critical_stock_repair"] = True
    if stock_chain_strategy != 'stock_first':
        options['stock_chain_strategy'] = stock_chain_strategy
    result = solve_local(problem, **options)
    return finish_result(problem, result, solver_module=solve_local.__module__)
