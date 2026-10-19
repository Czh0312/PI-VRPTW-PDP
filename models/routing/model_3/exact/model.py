"""MILP construction entry for this numbered model."""
from models.common.cycle.scope import validate_problem


def build_model(problem, **kwargs):
    """Build this model's complete fixed-assignment, vehicle-indexed MILP."""
    validate_problem(problem, 3)
    return _build_complete_model(problem, **kwargs)


def _build_complete_model(problem, *, verbose=False, deadline=None, share_e2=True, travel_bounds=None, domain_check=None,
                strengthen_multibatch=True, share_cutoff=True, e2_capacity_cuts=True,
                linear_e2_links=True, trip_symmetry=True, compress_multibatch_routes=None,
                factor_e2_loads=False, distance_cuts=None, _distance_certificate=None,
                single_batch_cycle_cuts=False, single_batch_causal_bounds=False):
    if not isinstance(single_batch_cycle_cuts,bool):
        raise ValueError('Short-cycle cuts must be boolean')
    if not isinstance(single_batch_causal_bounds,bool):
        raise ValueError('Causal step bounds must be boolean')
    if (single_batch_cycle_cuts or single_batch_causal_bounds) and len(problem.operating_policy.batches) != 1:
        raise ValueError('Short-cycle cuts require B=1')
    if len(problem.operating_policy.batches) == 1:
        from models.routing.model_3.exact.formulation import build_model as build_single
        cycle_options = dict(short_cycle_cuts=True) if single_batch_cycle_cuts else {}
        if single_batch_causal_bounds:
            cycle_options['causal_step_bounds'] = True
        return build_single(problem, verbose=verbose, deadline=deadline, share_e2=share_e2,
                            travel_bounds=travel_bounds, domain_check=domain_check, **cycle_options)
    raise ValueError('M3 requires a single-batch formulation')
