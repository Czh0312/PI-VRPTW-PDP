"""M6 public entry: explicit algorithm selection and stable result identity."""
import time

from models.common.cycle.scope import validate_problem
from models.common.solver_config import MilpOptions, record_strategy, validate_budget

from models.routing.model_6.exact.k_search.scheduler import solve_uniform as aggregate


def solve_exact(problem, time_limit=60, seed=42, verbose=False, initial_result=None,
                budget_strategy='adaptive', *, strategy='auto', milp_options=None):
    begun = time.perf_counter()
    validate_problem(problem, 6)
    if strategy not in ('auto', 'milp'):
        raise ValueError('Unsupported M6 exact strategy: '+str(strategy))
    if strategy != 'milp' and milp_options is not None:
        raise ValueError('milp_options requires strategy="milp"')
    if strategy == 'milp':
        validate_budget(time_limit, seed)
        if initial_result is not None:
            raise ValueError('Direct MILP baseline does not accept an external initial solution')
        options = MilpOptions.resolve(milp_options)
        from models.routing.model_6.exact.milp_solver import solve_milp
        def candidate(child, **kwargs):
            return solve_milp(child, milp_options=options, **kwargs)
        if not getattr(problem, 'uniform_cycle', False):
            result = candidate(problem, time_limit=time_limit, seed=seed, verbose=verbose)
        else:
            result = aggregate(problem, solve_candidate=candidate, method='exact', budget=time_limit,
                               seed=seed, verbose=verbose, budget_strategy='single_pass', analytical_bounds=False)
        # The complete candidate ledger is retained even when the budget expires.
        if 'count_search' in result:
            result['info']['solver_config'] = options.metadata(time_limit, seed)
            result['info']['solver_config']['retained_model_bounds'] = 'necessary_pool_and_total_cost_rows'
        result['info']['solver_config']['k_search'] = 'single_pass_no_external_bounds' if 'count_search' in result else None
    else:
        from models.routing.model_6.exact.enhanced_solver import solve_exact as solve_fixed
        if getattr(problem, 'uniform_cycle', False):
            result = aggregate(problem, solve_candidate=solve_fixed, method='exact', budget=time_limit,
                               seed=seed, verbose=verbose, initial_result=initial_result,
                               budget_strategy=budget_strategy, analytical_bounds=True)
        else:
            result = solve_fixed(problem, time_limit=time_limit, seed=seed, verbose=verbose,
                                 initial_result=initial_result, budget_strategy=budget_strategy)
    result['solver_module'] = __name__
    result['info']['runtime'] = time.perf_counter()-begun
    return record_strategy(result, strategy, time_limit=time_limit, seed=seed)
