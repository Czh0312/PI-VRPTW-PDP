"""Validate public model entry points and finish their shared solver results."""
from models.common.cycle.policy import VERSION


def batch_mode_for(problem):
    policy = problem.operating_policy
    if getattr(policy, 'policy_version', None) != VERSION:
        raise ValueError('Current model entries require multi_batch_cutoff_v1')
    return 'single_batch' if len(policy.batches) == 1 else 'multi_batch'


def require_scope(problem, model, batch_mode):
    if problem.model_number != model:
        raise ValueError(f'M{model} entry cannot run an M{problem.model_number} instance')
    if batch_mode_for(problem) != batch_mode:
        requirement = 'B=1' if batch_mode == 'single_batch' else 'B>1'
        raise ValueError(f'{batch_mode}/model_{model} requires {requirement}')
    problem.operating_policy.require_model(model)


def finish_result(problem, result, *, solver_module=None):
    mode = batch_mode_for(problem)
    if mode == 'single_batch' and result.get('audit', {}).get('valid'):
        cost = getattr(problem, 'cost_objective', False)
        # Consolidated handoff has no fixed recovery time. Report the earliest
        # execution of the chosen routes when occupied time carries no fee.
        unpriced_time = (problem.operating_policy.consolidated_handoff and cost
                         and all(problem.cost_profile.rates[f'{layer}_work_cost_per_minute'] == 0
                                 for layer in ('e1', 'e2')))
        if not cost or unpriced_time:
            from models.common.cycle.timing import normalize_schedule
            result = normalize_schedule(problem, result)
    result['batch_mode'] = mode
    result['model_module'] = f'models.routing.model_{problem.model_number}.{result["solver"]}'
    if solver_module is not None:
        result['solver_module'] = solver_module
    return result


def validate_problem(problem, model):
    """One model identity check, shared by its exact and ALNS entries."""
    if model in (1, 2, 3, 4):
        require_scope(problem, model, 'single_batch')
    elif model == 5:
        if problem.model_number != 5 or not getattr(problem, 'calendar_cycle', False):
            raise ValueError('Model 5 requires a CalendarCycleInstance')
    else:
        raise ValueError(f'Unknown model: {model}')


def require_fixed_calendar(problem):
    if (problem.model_number != 5 or not getattr(problem, 'joint_batch', False)
            or getattr(problem, 'calendar_cycle', False)):
        raise ValueError('Expected a fixed-count Model 5 calendar')
