"""Complete M5 MILP; batch_count is optional for fixed-count diagnostics."""
from models.common.cycle.scope import validate_problem
from functools import partial


def build_model(problem, *, batch_count=None, verbose=False, deadline=None, compact_e2=False,
                strengthening='physical_time', preprocessing=False, vehicle_symmetry=False):
    validate_problem(problem, 5)
    from models.routing.model_5.exact.formulation import build_model as build_fixed
    from models.routing.model_5.exact.constraints import validate_strengthening
    from models.routing.model_5.exact.preprocessing import (
        validate_preprocessing_options, infeasible_time_domain_certificate)
    validate_strengthening(strengthening)
    validate_preprocessing_options(preprocessing, vehicle_symmetry)
    build_fixed = partial(build_fixed, strengthening=strengthening,
                          preprocessing=preprocessing, vehicle_symmetry=vehicle_symmetry)
    if batch_count is not None:
        return build_fixed(problem.for_count(batch_count), verbose=verbose,
                           deadline=deadline, compact_e2=compact_e2)
    from models.common.cycle.unified_milp import build_unified_model
    return build_unified_model(problem, build_fixed=build_fixed, verbose=verbose,
                               deadline=deadline, compact_e2=compact_e2, count_screening=preprocessing,
                               count_certificate=infeasible_time_domain_certificate if preprocessing else None)
