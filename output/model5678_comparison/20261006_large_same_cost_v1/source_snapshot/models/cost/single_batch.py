"""Conditional, undiscounted lifecycle comparison of audited B=1 schedules."""
from copy import deepcopy
from dataclasses import asdict

from .multibatch import evaluate_cycle_cost
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.policy import integer, nonnegative
from models.routing.common.cycle.timing import normalize_schedule


def _basis(problem):
    fleet = problem.fleet
    return dict(orders=[asdict(o) for o in problem.orders], policy=problem.operating_policy.as_dict(),
                satellites=problem.satellites, depot=problem.depot,
                fleet={key: getattr(fleet, key) for key in (
                    'e1_count', 'e2_per_sat_count', 'e1_weight_cap', 'e1_volume_cap',
                    'e2_weight_cap', 'e2_volume_cap', 'container_weight', 'container_volume')},
                services={s: problem.service(s) for s in problem.satellites},
                network={(a, b): (problem.distance(a, b), problem.travel(a, b))
                         for a in problem.network.nodes for b in problem.network.nodes})


def lifecycle_pair(single_use, reusable, *, horizon, lifetime, packages, disposal_rate):
    """Repeat the same feasible cycle; replace the entire pool after L cycles.

    No loss, financing, discounting, inflation or salvage; these are declared
    scenario assumptions. Initial purchases are never added to amortization.
    """
    horizon = integer(horizon, 'evaluation_cycles', 1)
    lifetime = integer(lifetime, 'pool_lifetime_cycles', 1)
    investment = reusable['initial_pool_investment']
    pure, reuse = single_use['period_operating_cost'], reusable['period_operating_cost']
    savings = pure-reuse
    rows = []
    for t in range(1, horizon+1):
        purchases = (t-1)//lifetime+1
        a, b = pure*t, reuse*t+investment*purchases
        rows.append(dict(cycle=t, pool_purchases=purchases, single_use_total=a, reusable_total=b,
                         saving=a-b, reusable_no_more_expensive=a-b >= -1e-9))
    first = next((r['cycle'] for r in rows if r['reusable_no_more_expensive']), None)
    last_loss = max((r['cycle'] for r in rows if not r['reusable_no_more_expensive']), default=0)
    sustained = last_loss+1 if last_loss < horizon else None
    transport = sum(value for key, value in single_use['components'].items() if key.startswith(('e1_', 'e2_')))
    return dict(single_use_model=single_use['model'], reusable_model=reusable['model'],
                evaluation_cycles=horizon, pool_lifetime_cycles=lifetime,
                per_cycle_operating_saving=savings, initial_pool_investment=investment,
                first_break_even_within_horizon=first,
                no_more_expensive_from_cycle_through_horizon=sustained,
                lifetime_block_saving=lifetime*savings-investment,
                lifetime_average_single_use_purchase_price_threshold=(
                    (reuse+investment/lifetime-transport)/packages-disposal_rate if packages else None),
                single_use_total=rows[-1]['single_use_total'], reusable_total=rows[-1]['reusable_total'],
                horizon_saving=rows[-1]['saving'],
                single_use_cost_per_delivery=rows[-1]['single_use_total']/(horizon*packages) if packages else None,
                reusable_cost_per_delivery=rows[-1]['reusable_total']/(horizon*packages) if packages else None,
                timeline=rows)


def compare_single_batch(problems, results, config):
    if set(problems) != {1, 2, 3, 4} or set(results) != set(problems):
        raise ValueError('Comparison requires results for all four models, including failures')
    reference = _basis(problems[1])
    for model, problem in problems.items():
        if len(problem.operating_policy.batches) != 1 or problem.model_number != model or _basis(problem) != reference:
            raise ValueError('Models must share the same B=1 orders, calendar, fleet and network')
    horizon = integer(config['evaluation_cycles'], 'evaluation_cycles', 1)
    lifetime = integer(config['pool_lifetime_cycles'], 'pool_lifetime_cycles', 1)
    costs, failures, proofs = {}, {}, {}
    for model, problem in problems.items():
        result = results[model]
        audit = audit_solution(problem, result)
        proofs[model] = dict(status=result['info']['status'],
                             distance_proven_optimal=bool(result['info'].get('proven_optimal')),
                             proof_scope=result['info'].get('proof_scope'))
        if not audit['valid'] or result['info'].get('objective') is None:
            failures[model] = dict(status=result['info']['status'], audit_errors=audit['errors'])
            continue
        canonical = normalize_schedule(problem, result)
        # Lifecycle cash totals below replace, and never add to, amortization.
        rates = {key: value for key, value in config.items() if key != 'pool_amortization_cycles'}
        costs[model] = evaluate_cycle_cost(problem, canonical, rates)
    pairs = {}
    for pure, reuse in ((1, 3), (2, 4)):
        key = f'M{pure}_vs_M{reuse}'
        if pure not in costs or reuse not in costs:
            pairs[key] = dict(available=False, reason='Both audited feasible schedules are required')
            continue
        pairs[key] = dict(available=True, **lifecycle_pair(costs[pure], costs[reuse], horizon=horizon,
            lifetime=lifetime, packages=problems[pure].total,
            disposal_rate=config['single_use_disposal_cost_per_package']))
    sensitivity = []
    prices = config.get('single_use_package_price_grid', [config['single_use_package_price']])
    lifetimes = config.get('pool_lifetime_cycles_grid', [lifetime])
    if not isinstance(prices, list) or not prices or not isinstance(lifetimes, list) or not lifetimes:
        raise ValueError('Sensitivity grids must be nonempty lists')
    for price in prices:
        price = nonnegative(price, 'single_use_package_price_grid')
        for life in lifetimes:
            life = integer(life, 'pool_lifetime_cycles_grid', 1)
            for pure, reuse in ((1, 3), (2, 4)):
                if pure not in costs or reuse not in costs:
                    continue
                varied = deepcopy(costs[pure])
                new_purchase = problems[pure].total*price
                varied['period_operating_cost'] += new_purchase-varied['components']['single_use_purchase']
                varied['components']['single_use_purchase'] = new_purchase
                data = lifecycle_pair(varied, costs[reuse], horizon=horizon, lifetime=life,
                    packages=problems[pure].total, disposal_rate=config['single_use_disposal_cost_per_package'])
                data.pop('timeline')
                sensitivity.append(dict(single_use_package_price=price, **data))
    return dict(currency=config['currency'], source_type=config['source_type'], costs=costs,
                failures=failures, route_proofs=proofs, comparisons=pairs,
                sensitivity=sensitivity, sensitivity_scope='fixed_routes_and_schedule; packaging_price_and_lifetime_only',
                schedule_convention='earliest_feasible_fixed_route_B1', cost_is_optimized=False,
                claim='Conditional scenario arithmetic; not an empirical profitability proof.',
                assumptions=['Identical repeated demand and service windows each cycle',
                             'Same forward packaging weight and volume in all models',
                             'Full pool replaced after the stated number of cycles; no random losses',
                             'No discounting, financing, inflation or salvage value',
                             'Distance-optimal or heuristic routes, not cost-optimal routes',
                             'Earliest fixed-route schedules; no work-time minimization'])
