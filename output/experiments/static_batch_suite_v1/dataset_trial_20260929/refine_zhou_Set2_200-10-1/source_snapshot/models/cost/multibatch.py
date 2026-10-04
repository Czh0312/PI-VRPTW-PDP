"""Post-process an audited cycle; investment is separate from recurring costs."""
from __future__ import annotations
from models.routing.variants.model_3.multibatch.audit import audit_solution
from models.routing.variants.model_3.multibatch.policy import nonnegative, integer


def evaluate_cycle_cost(problem, result, config):
    audit = audit_solution(problem, result)
    if not audit['valid']:
        raise ValueError(f'Cannot price an invalid route: {audit["errors"]}')
    if config.get('cost_basis') != 'disjoint_distance_time_fixed':
        raise ValueError('Specify cost_basis=disjoint_distance_time_fixed; rates must not double-count costs')
    for key in ('currency', 'source_type', 'description'):
        if not isinstance(config.get(key), str) or not config[key]:
            raise ValueError(f'Missing cost metadata: {key}')
    fields = ['distance_scale_to_km', 'e1_distance_cost_per_km', 'e2_distance_cost_per_km',
              'e1_work_cost_per_minute', 'e2_work_cost_per_minute', 'e1_vehicle_cost_per_cycle',
              'e2_vehicle_cost_per_cycle', 'container_price', 'cleaning_cost_per_return', 'tracking_cost_per_use']
    rates = {key: nonnegative(config[key], key) for key in fields}
    if rates['distance_scale_to_km'] <= 0:
        raise ValueError('distance_scale_to_km must be positive and justified')
    parts = {}
    for layer in ('e1', 'e2'):
        parts[layer+'_distance'] = audit['distances'][layer]*rates['distance_scale_to_km']*rates[layer+'_distance_cost_per_km']
        parts[layer+'_work'] = audit['time_metrics']['working_minutes'][layer]*rates[layer+'_work_cost_per_minute']
        parts[layer+'_vehicles'] = audit[layer+'_vehicle_count']*rates[layer+'_vehicle_cost_per_cycle']
    parts['cleaning'] = audit['container_balance']['returned_to_depot']*rates['cleaning_cost_per_return']
    parts['tracking'] = audit['container_balance']['issued']*rates['tracking_cost_per_use']
    investment = problem.operating_policy.container_pool_size*rates['container_price']
    periods = config.get('pool_amortization_cycles')
    amortization = investment/integer(periods, 'pool_amortization_cycles', 1) if periods is not None else None
    operating = sum(parts.values())
    return dict(currency=config['currency'], source_type=config['source_type'], description=config['description'],
                cost_basis=config['cost_basis'], components=parts, period_operating_cost=operating,
                initial_pool_investment=investment, pool_amortization_per_cycle=amortization,
                period_cost_with_amortization=operating+amortization if amortization is not None else None,
                container_pool_size=problem.operating_policy.container_pool_size,
                container_uses=audit['container_balance']['issued'],
                route_objective_mode='distance', cost_is_optimized=False)
