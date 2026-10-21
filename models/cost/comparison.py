"""Price M3 distance schedules and M5–M8 cost schedules under one contract."""
from __future__ import annotations

from tools.configuration.loader import load_json
from models.cost.joint_batch import JointCostProfile
from models.common.cycle.audit import audit_solution


def evaluate_comparison_cost(problem, result, parameters, *, audit=None):
    if problem.model_number not in (3, 5, 6, 7, 8):
        raise ValueError('Matched joint-cost comparison requires M3 or M5–M8')
    joint = getattr(problem, 'joint_batch', False)
    optimized = joint or getattr(problem, 'optimize_pool', False)
    profile = JointCostProfile(load_json(parameters) if isinstance(parameters, str) else parameters)
    if optimized and profile.hash != problem.cost_profile.hash:
        raise ValueError('Joint replay requires the same economic profile as the saved solution')
    audited = audit_solution(problem, result) if audit is None else audit
    if not audited['valid']:
        raise ValueError(f'Cannot price an invalid route: {audited["errors"]}')
    pool = (result['container_pool_size'] if optimized
            else problem.operating_policy.container_pool_size)
    cost = profile.evaluate(audited, pool)
    cost.update(model=problem.model_number, container_pool_size=pool,
                minimum_pool_for_schedule=audited['minimum_pool_for_schedule'],
                route_objective_mode='cost' if optimized else 'distance',
                cost_is_optimized=optimized,
                pool_basis='optimized_pool' if optimized else 'configured_pool')
    if problem.model_number in (3, 5) and not optimized:
        # This changes only the price of the pool, never the distance solution.
        reference = profile.evaluate(audited, audited['minimum_pool_for_schedule'])
        reference.update(container_pool_size=audited['minimum_pool_for_schedule'],
                         pool_basis='minimum_pool_for_unchanged_schedule',
                         cost_is_optimized=False)
        cost['schedule_minimum_pool_reference'] = reference
    return cost
