"""Refine an existing full-domain dual witness without restarting its cover LP."""
import math
from pathlib import Path
import time


def refine_pricing_witness(problem, context, reference, *, deadline, seed=42,
                           verbose=False, pricing_export_path=None):
    from .multibatch_capacity_cuts import capacity_floor, capacity_subsets
    from .multibatch_route_pricing import RoutePricer
    from .multibatch_route_cg import dual_certificate, repair_category_duals
    begun = time.perf_counter()
    index = reference.get('best_witness')
    if index is None or reference['iterations'][index].get('phase') != 'distance':
        raise ValueError('Fixed-dual refinement requires a distance-pricing witness')
    previous = reference['iterations'][index]
    pi = {tuple(key): float(value) for key, value in previous['duals']}
    if set(pi) != {n['key'] for n in context['nodes']} or not all(math.isfinite(v) for v in pi.values()):
        raise ValueError('Fixed-dual refinement requires finite prices for every task')
    nu = min(0.0, float(previous['fleet_dual']))
    if not math.isfinite(nu): raise ValueError('Fixed-dual refinement requires a finite fleet price')
    cuts = [[tuple(key) for key in keys] for _, keys, _, _ in previous.get('subset_counts', [])]
    subset_keys, subset_required = capacity_subsets(problem, context, cuts)
    subsets = {sid: max(0.0, float(value)) for sid, _, _, value in previous.get('subset_counts', [])}
    categories = {bid: max(0.0, float(value)) for bid, _, value in previous.get('delivery_counts', [])}
    if not all(math.isfinite(v) for v in list(subsets.values())+list(categories.values())):
        raise ValueError('Fixed-dual refinement requires finite nonnegative capacity prices')
    if set(subsets) != set(subset_keys): raise ValueError('Inconsistent capacity subset indices')
    if not set(categories) <= {n['order'].batch for n in context['nodes'] if n['key'][1]=='d'}:
        raise ValueError('Unknown delivery batch price')
    required = {bid: capacity_floor(problem, [n for n in context['nodes']
                   if n['key'][1] == 'd' and n['order'].batch == bid]) for bid in categories}
    row = dict(satellite=context['satellite'], bound=0.0, status='TIME_LIMIT_BUILD',
               lp_converged=False, best_witness=None, iterations=[], columns=[],
               pricing_backend='milp', refinement='fixed_duals', subset_cuts=cuts)
    pricer = None
    try:
        if time.perf_counter() >= deadline: return row
        pricer = RoutePricer(problem, context, deadline=deadline, seed=seed, verbose=verbose,
                             subset_keys=subset_keys)
        remaining = deadline-time.perf_counter()
        if remaining <= 0: return row
        priced = pricer.price(pi, nu, time_limit=remaining, category_duals=categories,
                               subset_duals=subsets, reuse_budget=True)
        constants = math.fsum(required[bid]*value for bid,value in categories.items())
        constants += math.fsum(subset_required[sid]*value for sid,value in subsets.items())
        standard = dual_certificate(pi, nu, context['maximum_trips'], priced['lower_bound'])+constants
        repaired = repair_category_duals(context, pi, nu, priced)
        repaired['bound'] += constants
        lower = max(standard, repaired['bound'])
        witness = dict(phase='distance', method='integer_pricing',
            master_objective=previous.get('master_objective'),
            duals=[[list(key),value] for key,value in pi.items()], fleet_dual=nu,
            maximum_trips=context['maximum_trips'],
            pricing={key:value for key,value in priced.items() if key not in ('column','columns')},
            candidate_bound=lower, standard_candidate_bound=standard, dual_repair=repaired,
            new_column=priced['column'], new_columns=priced['columns'],
            delivery_counts=[[bid,required[bid],value] for bid,value in categories.items()])
        if subsets:
            witness['subset_counts']=[[sid,[list(key) for key in sorted(subset_keys[sid])],
                                      subset_required[sid],value] for sid,value in subsets.items()]
        row.update(bound=max(0.0,lower), status='FIXED_DUAL_REPRICED', iterations=[witness],
                   best_witness=0 if lower>0 else None, columns=priced['columns'],
                   pricing_variables=pricer.NumVars)
        # This is a dual certificate only: no new cover LP was optimized and
        # no LP convergence or full-M5 optimality is inferred from pricing.
        if pricing_export_path is not None and lower>0:
            path=Path(pricing_export_path);path.parent.mkdir(parents=True,exist_ok=True)
            pricer.write(str(path))
        return row
    except TimeoutError:
        return row
    finally:
        row['runtime']=time.perf_counter()-begun
        if pricer is not None: pricer.dispose()
