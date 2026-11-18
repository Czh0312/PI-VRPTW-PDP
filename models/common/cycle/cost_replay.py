"""Price audited schedules with a matched period cost profile."""
from __future__ import annotations

from tools.configuration.loader import load_json
from models.common.cycle.audit import audit_solution
from models.common.cycle.policy import integer
from models.common.period_cost import JointCostProfile


def single_batch_period_profile(parameters):
    """Lift legacy B=1 prices into the shared objective schema."""
    if 'container_pool_cost_per_period' in parameters:
        return parameters
    source_type = parameters['source_type']
    if source_type not in ('synthetic_mechanism', 'public_proxy_scenario', 'calibrated'):
        source_type = 'synthetic_mechanism' if parameters['currency'] == 'synthetic_units' else 'public_proxy_scenario'
    life = parameters.get('pool_amortization_cycles', parameters.get('pool_lifetime_cycles'))
    pool_fee = 0
    if life is not None and parameters.get('container_lifetime_uses') is None:
        pool_fee = parameters['container_price'] / integer(life, 'pool lifetime in periods', 1)
    return {'price_basis': parameters['description'],
            'evaluation_period': 'one operating cycle', 'pool_cost_basis': 'owned_capital_charge',
            'work_time_basis': 'occupied_route_minutes', 'container_pool_cost_per_period': pool_fee,
            'e1_trip_cost': 0, 'e2_trip_cost': 0, **parameters, 'source_type': source_type}


def evaluate_comparison_cost(problem, result, parameters, *, audit=None):
    if problem.model_number not in (1, 2, 3, 4, 5):
        raise ValueError('Matched period cost comparison requires M1–M5')
    joint = getattr(problem, 'joint_batch', False)
    optimized = joint
    cost_optimized = optimized or getattr(problem, 'cost_objective', False)
    profile = JointCostProfile(load_json(parameters) if isinstance(parameters, str) else parameters)
    if cost_optimized and profile.hash != problem.cost_profile.hash:
        raise ValueError('Joint replay requires the same economic profile as the saved solution')
    audited = audit_solution(problem, result) if audit is None else audit
    if not audited['valid']:
        raise ValueError(f'Cannot price an invalid route: {audited["errors"]}')
    pool = (result['container_pool_size'] if optimized else
            problem.operating_policy.container_pool_size if problem.reusable else 0)
    cost = profile.evaluate(audited, pool, reusable=problem.reusable)
    cost.update(model=problem.model_number, container_pool_size=pool,
                minimum_pool_for_schedule=audited['minimum_pool_for_schedule'],
                route_objective_mode='cost' if cost_optimized else 'distance',
                cost_is_optimized=cost_optimized,
                pool_basis=('optimized_pool' if optimized else 'configured_pool' if problem.reusable else 'single_use'))
    if problem.model_number in (3, 5) and not cost_optimized:
        # This changes only the price of the pool, never the distance solution.
        reference = profile.evaluate(audited, audited['minimum_pool_for_schedule'])
        reference.update(container_pool_size=audited['minimum_pool_for_schedule'],
                         pool_basis='minimum_pool_for_unchanged_schedule',
                         cost_is_optimized=False)
        cost['schedule_minimum_pool_reference'] = reference
    return cost
