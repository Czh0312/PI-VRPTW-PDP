"""MILP construction entry for this numbered model."""
from models.common.cycle.scope import validate_problem


def build_joint_model(problem, **kwargs):
    """Build the full joint assignment/routing MILP for this numbered model."""
    validate_problem(problem, 2)
    return _build_complete_model(problem, **kwargs)


def build_subproblem(problem, assignment, **kwargs):
    """Build one assignment MILP; global exact search must cover all assignments."""
    validate_problem(problem, 2)
    from models.common.cycle.assignment import assigned_problem
    return _build_complete_model(assigned_problem(problem, assignment), **kwargs)


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
        from models.routing.model_2.exact.formulation import build_model as build_single
        cycle_options = dict(short_cycle_cuts=True) if single_batch_cycle_cuts else {}
        if single_batch_causal_bounds:
            cycle_options['causal_step_bounds'] = True
        return build_single(problem, verbose=verbose, deadline=deadline, share_e2=share_e2,
                            travel_bounds=travel_bounds, domain_check=domain_check, **cycle_options)
    raise ValueError('M2 requires a single-batch formulation')
