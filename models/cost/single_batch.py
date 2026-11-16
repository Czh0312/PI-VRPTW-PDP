"""Period costs and conditional lifecycle comparison of audited B=1 schedules."""
from dataclasses import asdict

from .comparison import evaluate_comparison_cost
from .multibatch import evaluate_cycle_cost
from models.common.cycle.audit import audit_solution
from models.common.cycle.policy import integer, nonnegative
from models.common.cycle.timing import normalize_schedule


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
    """Repeat the same feasible cycle with ownership or rental costs.

    No loss, financing, discounting, inflation or salvage; these are declared
    scenario assumptions. Initial purchases are never added to amortization.
    """
    horizon = integer(horizon, 'evaluation_cycles', 1)
    rental = reusable.get('pool_cost_basis') == 'rental'
    lifetime = None if rental else integer(lifetime, 'pool_lifetime_cycles', 1)
    investment = 0 if rental else reusable['initial_pool_investment']
    pure, reuse = single_use['period_operating_cost'], reusable['period_operating_cost']
    savings = pure-reuse
    rows = []
    for t in range(1, horizon+1):
        purchases = (t-1)//lifetime+1 if lifetime else 0
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
                lifetime_block_saving=lifetime*savings-investment if lifetime else None,
                lifetime_average_single_use_purchase_price_threshold=(
                    (reuse + (investment/lifetime if lifetime else 0) - transport)/packages-disposal_rate
                    if packages else None),
                single_use_total=rows[-1]['single_use_total'], reusable_total=rows[-1]['reusable_total'],
                horizon_saving=rows[-1]['saving'],
                single_use_cost_per_delivery=rows[-1]['single_use_total']/(horizon*packages) if packages else None,
                reusable_cost_per_delivery=rows[-1]['reusable_total']/(horizon*packages) if packages else None,
                timeline=rows)


def incidental_cost_sensitivity(pair, *, packages, loss_rates, replacement_cost_per_box,
                                other_cost_per_use=0):
    """Add expected incremental spending to the costs of an audited pair."""
    packages = integer(packages, 'packages')
    replacement = nonnegative(replacement_cost_per_box, 'replacement_cost_per_box')
    other = nonnegative(other_cost_per_use, 'other_cost_per_use')
    rates = [nonnegative(rate, 'loss_rate') for rate in loss_rates]
    if not rates or any(rate > 1 for rate in rates):
        raise ValueError('Provide at least one loss rate between 0 and 1')

    def max_loss_rate(saving, uses):
        remaining = saving - uses*other
        if not uses or remaining < -1e-9:
            return None
        if not replacement:
            return 1.0
        return min(1.0, max(0.0, remaining/(uses*replacement)))

    horizon = pair.get('evaluation_cycles')
    baseline_saving = pair['period_saving']
    horizon_saving = pair.get('horizon_saving')
    paper_price = pair.get('period_single_use_purchase_price_threshold')
    scenarios = []
    for rate in rates:
        per_use = rate*replacement + other
        added = packages*per_use
        scenarios.append(dict(loss_rate=rate, incidental_cost_per_use=per_use,
                              period_incidental_cost=added, period_saving=baseline_saving-added,
                              period_paper_price_threshold=(paper_price+per_use if paper_price is not None else None),
                              horizon_saving=(horizon_saving-horizon*added if horizon is not None else None)))
    return dict(packages_per_period=packages, replacement_cost_per_box=replacement, other_cost_per_use=other,
                period_max_loss_rate=max_loss_rate(baseline_saving, packages),
                horizon_max_loss_rate=(max_loss_rate(horizon_saving, horizon*packages)
                                       if horizon is not None else None),
                scenarios=scenarios)


def compare_single_batch(problems, results, config):
    if set(problems) != {1, 2, 3, 4} or set(results) != set(problems):
        raise ValueError('Comparison requires results for all four models, including failures')
    reference = _basis(problems[1])
    for model, problem in problems.items():
        if len(problem.operating_policy.batches) != 1 or problem.model_number != model or _basis(problem) != reference:
            raise ValueError('Models must share the same B=1 orders, calendar, fleet and network')
    matched_profile = 'container_pool_cost_per_period' in config
    pool_basis = config.get('pool_cost_basis')
    horizon = config.get('evaluation_cycles') if matched_profile else config['evaluation_cycles']
    lifetime = config.get('pool_lifetime_cycles') if matched_profile else config['pool_lifetime_cycles']
    if horizon is not None:
        horizon = integer(horizon, 'evaluation_cycles', 1)
    if lifetime is not None:
        lifetime = integer(lifetime, 'pool_lifetime_cycles', 1)
    lifecycle_available = horizon is not None and (
        pool_basis == 'rental' or (pool_basis in (None, 'owned_capital_charge') and lifetime is not None))
    disposal_rate = config.get('single_use_disposal_cost_per_package', 0)
    costs, failures, proofs = {}, {}, {}
    for model, problem in problems.items():
        result = results[model]
        audit = audit_solution(problem, result)
        objective_mode = result.get('objective_mode', 'distance')
        proven = audit['valid'] and bool(result['info'].get('proven_optimal'))
        proofs[model] = dict(status=result['info']['status'],
                             objective_mode=objective_mode, objective_proven_optimal=proven,
                             distance_proven_optimal=proven and objective_mode == 'distance',
                             proof_scope=result['info'].get('proof_scope'))
        if not audit['valid'] or result['info'].get('objective') is None:
            failures[model] = dict(status=result['info']['status'], audit_errors=audit['errors'])
            continue
        if objective_mode == 'distance':
            result = normalize_schedule(problem, result)
            audit = result['audit']
        if matched_profile:
            cost = evaluate_comparison_cost(problem, result, config, audit=audit)
            cost['pool_cost_basis'] = pool_basis
            cost['period_operating_cost'] = cost['total']
            if pool_basis == 'owned_capital_charge' and config.get('container_lifetime_uses') is not None:
                cost['period_operating_cost'] -= cost['components'].get('container_wear', 0)
        else:
            if objective_mode == 'cost':
                raise ValueError('Cost results require their matching period economic profile')
            rates = {key: value for key, value in config.items() if key != 'pool_amortization_cycles'}
            cost = evaluate_cycle_cost(problem, result, rates)
            cost['total'] = cost['period_operating_cost']
        cost['schedule_convention'] = result.get('schedule_convention', 'audited_solver_schedule')
        costs[model] = cost
    pairs = {}
    for pure, reuse in ((1, 3), (2, 4)):
        key = f'M{pure}_vs_M{reuse}'
        if pure not in costs or reuse not in costs:
            pairs[key] = dict(available=False, reason='Both audited feasible schedules are required')
            continue
        paper_cost, reusable_cost = costs[pure]['total'], costs[reuse]['total']
        paper_purchase = costs[pure]['components']['single_use_purchase']
        saving = paper_cost - reusable_cost
        packages = problems[pure].total
        pair = dict(available=True, single_use_model=pure, reusable_model=reuse,
                    single_use_period_cost=paper_cost, reusable_period_cost=reusable_cost,
                    period_saving=saving, period_saving_per_delivery=saving/packages if packages else None,
                    period_single_use_purchase_price_threshold=(paper_purchase-saving)/packages if packages else None,
                    lifecycle_available=lifecycle_available)
        if lifecycle_available:
            pair.update(lifecycle_pair(costs[pure], costs[reuse], horizon=horizon, lifetime=lifetime,
                                       packages=packages, disposal_rate=disposal_rate))
        pairs[key] = pair
    sensitivity = []
    if lifecycle_available:
        prices = config.get('single_use_package_price_grid', [config['single_use_package_price']])
        lifetimes = [None] if pool_basis == 'rental' else config.get('pool_lifetime_cycles_grid', [lifetime])
        if not isinstance(prices, list) or not prices or not isinstance(lifetimes, list) or not lifetimes:
            raise ValueError('Sensitivity grids must be nonempty lists')
        for price in prices:
            price = nonnegative(price, 'single_use_package_price_grid')
            for life in lifetimes:
                if pool_basis != 'rental':
                    life = integer(life, 'pool_lifetime_cycles_grid', 1)
                for pair in pairs.values():
                    if not pair['available']:
                        continue
                    pure, reuse = pair['single_use_model'], pair['reusable_model']
                    packages = problems[pure].total
                    components = {**costs[pure]['components'], 'single_use_purchase': packages*price}
                    operating = sum(components.values())
                    varied = dict(costs[pure], components=components, total=operating, period_operating_cost=operating)
                    data = lifecycle_pair(varied, costs[reuse], horizon=horizon, lifetime=life,
                                          packages=packages, disposal_rate=disposal_rate)
                    data.pop('timeline')
                    sensitivity.append(dict(single_use_package_price=price, **data))
    conventions = {cost['schedule_convention'] for cost in costs.values()}
    return dict(currency=config['currency'], source_type=config['source_type'], costs=costs,
                failures=failures, route_proofs=proofs, comparisons=pairs,
                sensitivity=sensitivity, sensitivity_scope='fixed_routes_and_schedule; packaging_price_and_lifetime_only',
                schedule_convention=next(iter(conventions)) if len(conventions) == 1 else None,
                cost_is_optimized=bool(costs) and all(cost['cost_is_optimized'] for cost in costs.values()),
                claim='Conditional costs for the supplied operating and pricing scenario.',
                assumptions=['Identical repeated demand and service windows each cycle',
                             'Same forward packaging weight and volume in all models',
                             'Owned pools are replaced after the stated number of cycles; no random losses',
                             'No discounting, financing, inflation or salvage value'])
