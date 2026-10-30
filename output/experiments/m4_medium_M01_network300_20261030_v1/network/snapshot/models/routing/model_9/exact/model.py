"""M9's full assignment/calendar MILP, or an explicit fixed-K diagnostic."""
from functools import partial

from models.common.cycle.scope import validate_problem


def build_model(problem, *, batch_count=None, verbose=False, deadline=None,
                compact_e2=False, strengthening='physical_time'):
    validate_problem(problem, 9)
    from models.routing.model_9.exact.formulation import build_model as build_fixed
    from models.routing.model_9.exact.constraints import validate_strengthening
    validate_strengthening(strengthening)
    build_fixed = partial(build_fixed, strengthening=strengthening)
    if batch_count is not None:
        return build_fixed(problem.for_count(batch_count), verbose=verbose,
                           deadline=deadline, compact_e2=compact_e2)
    from models.common.cycle.unified_milp import build_unified_model
    return build_unified_model(problem, build_fixed=build_fixed, verbose=verbose,
                               deadline=deadline, compact_e2=compact_e2)
