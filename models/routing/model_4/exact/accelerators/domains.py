"""Safe customer assignment domains for the single-batch free-assignment model.

Every deletion uses a necessary condition with relaxed shortest travel times.
No nearest-satellite restriction or metric assumption is used.
"""
from collections import defaultdict

from models.common.cycle.precheck import travel_lower_bounds


def assignment_domains(problem, travel_bounds=None):
    p, f = problem.operating_policy, problem.fleet
    if len(p.batches) != 1:
        raise ValueError('Assignment domains require B=1')
    batch = p.batches[0]
    travel_bounds = travel_bounds or travel_lower_bounds(problem)
    outward, inward = travel_bounds['outward'], travel_bounds['inward']
    grouped = defaultdict(list)
    for order in problem.orders:
        grouped[order.customer].append(order)
    errors, domains, excluded, windows = [], {}, [], {}
    weight = sum(o.goods_weight+o.count*f.container_weight for o in problem.orders)
    volume = problem.total*f.container_volume
    if weight > f.e1_count*(f.e1_weight_cap+1e-6) or volume > f.e1_count*(f.e1_volume_cap+1e-6):
        errors.append(dict(code='TOTAL_E1_DELIVERY_CAPACITY'))
    if problem.reusable and p.container_pool_size < problem.total:
        errors.append(dict(code='POOL_BELOW_NECESSARY_BOUND', lower_bound=problem.total))
    for customer, orders in sorted(grouped.items()):
        domains[customer] = []
        for site in problem.satellites:
            reasons = set()
            if (sum(o.goods_weight+o.count*f.container_weight for o in orders) > f.e1_weight_cap+1e-6 or
                    sum(o.count*f.container_volume for o in orders) > f.e1_volume_cap+1e-6):
                reasons.add('UNSPLIT_CUSTOMER_E1_CAPACITY')
            supply = batch.start+p.e1_load_time+outward[problem.depot][site]+problem.service(site)
            if supply > batch.cutoff:
                reasons.add('E1_DELIVERY_DEADLINE')
            for o in orders:
                _, w, v = problem.task(o.id, 'd')
                if w > f.e2_weight_cap+1e-6 or v > f.e2_volume_cap+1e-6:
                    reasons.add('UNSPLIT_E2_ORDER_CAPACITY')
                delivered = max(o.delivery_window[0], supply+p.e2_dispatch_prep_time+outward[site][customer])
                due = min(o.delivery_window[1], batch.cutoff-o.service_time)
                windows[site, o.id, 'd'] = (delivered, due)
                if delivered > due:
                    reasons.add('DELIVERY_WINDOW_UNREACHABLE')
                if problem.reusable:
                    picked = max(o.pickup_window[0], delivered+o.service_time+o.min_use_time)
                    latest = min(o.pickup_window[1], batch.cutoff-o.service_time-inward[site][customer]-p.e2_return_unload_time)
                    windows[site, o.id, 'p'] = (picked, latest)
                    if picked > latest:
                        reasons.add('PICKUP_OR_CUTOFF_UNREACHABLE')
                    returned = picked+o.service_time+inward[site][customer]+p.e2_return_unload_time
                else:
                    returned = delivered+o.service_time+inward[site][customer]+p.e2_return_unload_time
                if returned+p.e2_turnaround_time > p.cycle_end:
                    reasons.add('E2_TERMINAL_UNREACHABLE')
            if problem.reusable:
                returned = batch.cutoff+problem.service(site)+outward[site][problem.depot]+p.depot_return_unload_time
                if (returned > batch.end or returned+p.e1_turnaround_time > p.cycle_end
                        or returned+p.cleaning_duration > p.cycle_end):
                    reasons.add('NO_POSSIBLE_CLEAN_RETURN')
            if reasons:
                excluded.append(dict(customer=customer, satellite=site, reasons=sorted(reasons)))
            else:
                domains[customer].append(site)
        if not domains[customer]:
            errors.append(dict(code='NO_FEASIBLE_SATELLITE_DOMAIN', customer=customer))
    return dict(passed=not errors, proven_infeasible=bool(errors), errors=errors,
                proof_scope='single_batch_customer_assignment_necessary_conditions',
                domains=domains, excluded=excluded, windows=windows,
                limitation='Nonempty domains do not prove joint feasibility.')


def public_domain_check(check):
    """Keep tuple-key internal windows out of the JSON result contract."""
    return {k: v for k, v in check.items() if k != 'windows'}
