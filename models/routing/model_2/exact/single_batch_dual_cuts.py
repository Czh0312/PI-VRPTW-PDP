"""Optional E2 distance inequalities from certified delivery pricing.

For each site, pricing proves either rc(r) >= delta or rc(r) >= beta*|r|.
Summing over actual trips retains assignment and trip-count coefficients.
Unlike a constant objective floor, these inequalities also constrain branches
with different customer assignments. No restricted-pool bound is transferred.
Time-penalized pricing is deliberately unsupported here.
"""
from __future__ import annotations

from collections import defaultdict
import math


def make_dual_cuts(contexts, prices, pricing_bounds, task_floors, *, iteration, margin):
    cuts = []
    for kind in ('task', 'route'):
        terms = defaultdict(list)
        trips = []
        for site, context in contexts.items():
            cover, fleet = prices[site]
            for node in context['nodes']:
                terms[site, node['order'].customer].append(cover[node['key']]
                    +(task_floors[site] if kind == 'task' else 0))
            trips.append([site, fleet+(min(0.0, pricing_bounds[site]) if kind == 'route' else 0)])
        cuts.append(dict(kind=kind, iteration=iteration, margin=margin,
            assignment_terms=[[site, customer, math.fsum(values)]
                              for (site, customer), values in sorted(terms.items())],
            trip_terms=trips))
    return cuts


def _finite(value):
    return not isinstance(value, bool) and isinstance(value, (float, int)) and math.isfinite(value)


def apply_dual_cuts(model, problem, ctx, certificate):
    """Called only after the certificate's scope and fingerprint are checked."""
    import gurobipy as gp
    from models.routing.model_2.exact.single_batch_columns import problem_fingerprint
    if (certificate.get('proof_scope') != 'B1_delivery_route_relaxation'
            or problem.model_number not in (1, 2) or problem.reusable
            or len(problem.operating_policy.batches) != 1
            or certificate.get('problem_sha256') != problem_fingerprint(problem)):
        raise ValueError('Dual-cut certificate does not match this delivery problem')
    rows = certificate.get('dual_cuts', [])
    if not rows:
        return 0
    if certificate.get('time_cuts'):
        raise ValueError('Assignment dual cuts require unpenalized delivery pricing')
    # Each global internal arc is shared across route copies and warehouses.
    # Count it once; route boundary arcs are distinct. Directed costs matter.
    model.update()
    costs, trip_vars = {}, defaultdict(list)
    for route in ctx['e2']:
        trip_vars[route['satellite']].append(route['used'])
        for (left, right), variable in route['x'].items():
            a = route['base'] if left == -1 else route['nodes'][left]['order'].customer
            b = route['base'] if right == -2 else route['nodes'][right]['order'].customer
            cost = problem.distance(a, b)
            if variable.index in costs and costs[variable.index][1] != cost:
                raise ValueError('Shared E2 arc has inconsistent directed costs')
            costs[variable.index] = variable, cost
    distance = model.addVar(lb=0, name='B1_dual_E2_distance')
    model.addConstr(distance == gp.quicksum(v*c for v, c in costs.values()),
                    name='B1_dual_E2_distance_definition')
    eligible = {(r['satellite'], n['order'].customer) for r in ctx['e2'] for n in r['nodes']}
    for index, cut in enumerate(rows):
        margin = cut['margin']
        if not _finite(margin) or margin < 0:
            raise ValueError('Dual-cut margin must be finite and nonnegative')
        rhs = gp.LinExpr(-margin)
        seen = set()
        for site, customer, coefficient in cut['assignment_terms']:
            key = site, customer
            if key in seen or key not in eligible or not _finite(coefficient):
                raise ValueError('Invalid dual-cut assignment term')
            seen.add(key)
            rhs += coefficient*(ctx['assignments'][customer, site] if problem.free_assignment else 1)
        seen.clear()
        for site, coefficient in cut['trip_terms']:
            if site in seen or site not in trip_vars or not _finite(coefficient) or coefficient > 0:
                raise ValueError('Dual-cut trip price must be finite and nonpositive')
            seen.add(site)
            rhs += coefficient*gp.quicksum(trip_vars[site])
        model.addConstr(distance >= rhs, name=f'B1_certified_E2_dual_{index}')
    return len(rows)
