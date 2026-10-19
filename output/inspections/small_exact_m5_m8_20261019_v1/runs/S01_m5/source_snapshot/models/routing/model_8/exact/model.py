"""Complete M8 MILP; batch_count is optional for fixed-count diagnostics."""
from models.common.cycle.scope import validate_problem


def build_model(problem, *, batch_count=None, verbose=False, deadline=None, compact_e2=False):
    validate_problem(problem, 8)
    from models.routing.model_8.exact.formulation import build_model as build_fixed
    if not (getattr(problem, 'uniform_cycle', False) or getattr(problem, 'calendar_cycle', False)):
        if batch_count is not None:
            raise ValueError('batch_count requires a variable-count instance')
        return build_fixed(problem, verbose=verbose, deadline=deadline, compact_e2=compact_e2)
    if batch_count is not None:
        return build_fixed(problem.for_count(batch_count), verbose=verbose,
                           deadline=deadline, compact_e2=compact_e2)
    from models.common.cycle.unified_milp import build_unified_model
    return build_unified_model(problem, build_fixed=build_fixed, verbose=verbose,
                               deadline=deadline, compact_e2=compact_e2)
