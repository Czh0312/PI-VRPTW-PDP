"""Per-period cost comparison of audited B=1 schedules."""
from dataclasses import asdict

from models.common.cycle.audit import audit_solution
from models.common.cycle.cost_replay import evaluate_comparison_cost, single_batch_period_profile
from models.common.cycle.policy import integer, nonnegative
from models.common.cycle.timing import normalize_schedule


def _basis(problem):
    fleet = problem.fleet
    policy = problem.operating_policy.as_dict()
    if len(policy['batches']) == 1:
        batch = policy['batches'][0]
        # End-of-window delivery and collection after the last unload share
        # an outer window without a fixed interior cutoff.
        if batch['cutoff'] is None or (not problem.reusable and batch['cutoff'] == batch['end']):
            batch['cutoff'] = None
            policy.pop('pickup_policy')
    return dict(orders=[asdict(o) for o in problem.orders], policy=policy,
                satellites=problem.satellites, depot=problem.depot,
                fleet={key: getattr(fleet, key) for key in (
                    'e1_count', 'e2_per_sat_count', 'e1_weight_cap', 'e1_volume_cap',
                    'e2_weight_cap', 'e2_volume_cap', 'container_weight', 'container_volume')},
                services={s: problem.service(s) for s in problem.satellites},
                network={(a, b): (problem.distance(a, b), problem.travel(a, b))
                         for a in problem.network.nodes for b in problem.network.nodes})


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

    baseline_saving = pair['period_saving']
    paper_price = pair.get('period_single_use_purchase_price_threshold')
    scenarios = []
    for rate in rates:
        per_use = rate*replacement + other
        added = packages*per_use
        scenarios.append(dict(loss_rate=rate, incidental_cost_per_use=per_use,
                              period_incidental_cost=added, period_saving=baseline_saving-added,
                              period_paper_price_threshold=(paper_price+per_use if paper_price is not None else None)))
    return dict(packages_per_period=packages, replacement_cost_per_box=replacement, other_cost_per_use=other,
                period_max_loss_rate=max_loss_rate(baseline_saving, packages),
                scenarios=scenarios)


def compare_single_batch(problems, results, config):
    if set(problems) != {1, 2, 3, 4} or set(results) != set(problems):
        raise ValueError('Comparison requires results for all four models, including failures')
    reference = _basis(problems[1])
    for model, problem in problems.items():
        if len(problem.operating_policy.batches) != 1 or problem.model_number != model or _basis(problem) != reference:
            raise ValueError('Models must share the same B=1 orders, calendar, fleet and network')
    matched_profile = 'container_pool_cost_per_period' in config
    config = single_batch_period_profile(config)
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
        if objective_mode == 'cost' and not matched_profile:
            raise ValueError('Cost results require their matching period economic profile')
        cost = evaluate_comparison_cost(problem, result, config, audit=audit)
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
                    period_single_use_purchase_price_threshold=(paper_purchase-saving)/packages if packages else None)
        pairs[key] = pair
    sensitivity = []
    prices = config.get('single_use_package_price_grid', [])
    if not isinstance(prices, list):
        raise ValueError('single_use_package_price_grid must be a list')
    for price in prices:
        price = nonnegative(price, 'single_use_package_price_grid')
        for pair in pairs.values():
            if pair['available']:
                pure = pair['single_use_model']
                difference = price*problems[pure].total - costs[pure]['components']['single_use_purchase']
                sensitivity.append(dict(single_use_model=pure, reusable_model=pair['reusable_model'],
                                        single_use_package_price=price, period_saving=pair['period_saving']+difference))
    conventions = {cost['schedule_convention'] for cost in costs.values()}
    return dict(currency=config['currency'], source_type=config['source_type'], costs=costs,
                failures=failures, route_proofs=proofs, comparisons=pairs,
                sensitivity=sensitivity, sensitivity_scope='fixed_routes_and_schedule; single_use_package_price',
                schedule_convention=next(iter(conventions)) if len(conventions) == 1 else None,
                cost_is_optimized=bool(costs) and all(cost['cost_is_optimized'] for cost in costs.values()),
                claim='Per-period costs for the audited schedules and stated prices.')
