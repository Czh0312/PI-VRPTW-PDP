"""Repair a full-domain pricing dual using delivery-count row multipliers.

This only changes the dual certificate. It adds no route restriction or cut.
For each delivery category, reducing its nonnegative count-row multiplier
increases every route's reduced cost by that same reduction. Remaining
deficits are covered by task-price and fleet-price reductions.
"""
from collections import Counter
import math


def repair_count_duals(context, duals, fleet_dual, pricing, delivery_counts):
    nodes = context['nodes']
    keys = {n['key'] for n in nodes}
    expected = {None} | {n['order'].batch for n in nodes if n['key'][1] == 'd'}
    reports = pricing['groups']
    if set(duals) != keys:
        raise ValueError('Count dual repair requires every task multiplier')
    if {g['category'] for g in reports} != expected or len(reports) != len(expected):
        raise ValueError('Count dual repair requires every complete route category')
    if set(delivery_counts) != expected-{None}:
        raise ValueError('Count dual repair requires every delivery-count row')
    counts = Counter(None if n['key'][1] == 'p' else n['order'].batch for n in nodes)
    rows = {}
    for bid, (required, multiplier) in delivery_counts.items():
        if isinstance(required, bool) or not isinstance(required, int) or required < 1:
            raise ValueError('Delivery count must be a positive integer')
        multiplier = float(multiplier)
        if not math.isfinite(multiplier) or multiplier < 0:
            raise ValueError('Delivery-count multipliers must be finite and nonnegative')
        rows[bid] = (required, multiplier)
    original_nu = float(fleet_dual)
    if not math.isfinite(original_nu):
        raise ValueError('Count dual repair requires a finite fleet multiplier')
    nu = min(0.0, original_nu)
    bounds = {g['category']: float(g['lower_bound']) for g in reports if not g['infeasible']}
    if not all(math.isfinite(v) for v in list(duals.values())+[nu]+list(bounds.values())):
        raise ValueError('Count dual repair requires finite prices and bounds')
    candidates = {0.0}
    for category, rho in bounds.items():
        candidates.add(max(0.0, -rho))
        if category in rows and rows[category][0] <= counts[category]:
            candidates.add(max(0.0, -rho-rows[category][1]))
    best = None
    for shift in sorted(candidates):
        corrections, reductions = {}, {}
        for category, rho in bounds.items():
            need = max(0.0, -rho-shift)
            required, multiplier = rows.get(category, (0, 0.0))
            reduction = min(need, multiplier) if category in rows and required <= counts[category] else 0.0
            reductions[category] = reduction
            corrections[category] = need-reduction
        repaired = {n['key']: float(duals[n['key']])-corrections.get(
            None if n['key'][1] == 'p' else n['order'].batch, 0.0) for n in nodes}
        count_loss = math.fsum(required*reductions.get(bid, 0.0)
                               for bid, (required, _) in rows.items())
        # The caller adds the ORIGINAL count/subset objective terms. Deduct
        # exactly the removed count-row contribution here, so it cannot be
        # counted twice. Subset multipliers are retained without modification.
        value = math.fsum(repaired.values())+context['maximum_trips']*(nu-shift)-count_loss
        if best is None or value > best['bound']:
            best = dict(bound=value, repair_mode='delivery_count_first',
                fleet_shift=shift, repaired_fleet_dual=nu-shift,
                category_shifts=[[category, value] for category, value in corrections.items()],
                repaired_duals=[[list(key), value] for key, value in repaired.items()],
                delivery_count_shifts=[[bid, reductions.get(bid, 0.0)] for bid in rows],
                repaired_delivery_duals=[[bid, multiplier-reductions.get(bid, 0.0)]
                                         for bid, (_, multiplier) in rows.items()])
    return best
