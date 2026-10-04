"""Research-only balanced duals; fresh complete pricing is always required."""
import math
import time

from models.routing.solvers.exact.multibatch_route_pricing import RoutePricer


def transformed_prices(problem, history, balanced):
    if problem.model_number != 4 or len(problem.operating_policy.batches) != 1:
        raise ValueError('Pair-price diagnostic requires B1M4')
    if not isinstance(balanced, bool): raise ValueError('balanced must be boolean')
    if history['phase'] != 2: raise ValueError('Distance phase required')
    prices = {}
    for site, record in history['prices'].items():
        pi = {tuple(k): float(v) for k, v in record['tasks']}
        if balanced:
            for order in {k[0] for k in pi}:
                if (order, 'd') not in pi or (order, 'p') not in pi:
                    raise ValueError('Both original order tasks must remain')
                mean = math.fsum([pi[order, 'd'], pi[order, 'p']])/2
                pi[order, 'd'] = pi[order, 'p'] = mean
        prices[site] = (pi, min(0.0, float(record['fleet'])))
    gamma = {}
    for customer, old in history['assignment_duals'].items():
        keys = [(o.id, k) for o in problem.orders if o.customer == customer for k in ('d', 'p')]
        totals = [math.fsum(pi[k] for k in keys) for pi, _ in prices.values() if all(k in pi for k in keys)]
        if not totals: raise ValueError('Missing shared customer assignment domain')
        gamma[customer] = min(float(old), *totals)
    return prices, gamma


def explicit_dual_bound(prices, gamma, counts, rho):
    # All rho values certify the NEW prices over the entire route universe.
    dual = math.fsum(gamma.values())+math.fsum(counts[s]*nu for s, (_, nu) in prices.items())
    raw = dual+math.fsum(counts[s]*min(0.0, rho[s]) for s in prices)
    norm = math.fsum(abs(v) for v in gamma.values())
    norm += math.fsum(abs(v) for pi, nu in prices.values() for v in list(pi.values())+[nu])
    margin = 1e-7*max(1.0, abs(raw), norm)*(1+sum(counts.values()))
    return dict(explicit_dual_objective=dual, raw_corrected=raw, margin=margin,
                corrected_e2_bound=raw-margin, e2_bound=max(0.0, raw-margin))


def price_all(problem, contexts, history, counts, *, balanced, seed, deadline, call_cap=1.0, verbose=False):
    prices, gamma = transformed_prices(problem, history, balanced)
    if set(prices) != set(contexts) or set(prices) != set(counts):
        raise ValueError('All eligible warehouse task universes must be covered')
    for s, (pi, _) in prices.items():
        if set(pi) != set(contexts[s]['lookup']): raise ValueError('Original task domain mismatch')
    rho = {s: -math.fsum(max(0.0, v) for v in pi.values())-nu for s, (pi, nu) in prices.items()}
    pricers = {}; records = {}; begun = time.perf_counter(); build = 0.0
    try:
        for s, ctx in contexts.items():
            if time.perf_counter() >= deadline: break
            t = time.perf_counter()
            try: pricers[s] = RoutePricer(problem, ctx, deadline=deadline, seed=seed, verbose=verbose)
            except TimeoutError: break
            finally: build += time.perf_counter()-t
        for s, (pi, nu) in prices.items():
            remaining = deadline-time.perf_counter()
            if s not in pricers or remaining <= 0: continue
            rec = pricers[s].price(pi, nu, time_limit=min(call_cap, remaining),
                                   phase_one=False, stop_at_negative=False)
            records[s] = rec; rho[s] = 0.0 if rec['infeasible'] else rec['lower_bound']
    finally:
        for pricer in pricers.values(): pricer.dispose()
    complete = len(records) == len(contexts) and all(r['optimal'] or r['infeasible'] for r in records.values())
    return dict(prices={s: dict(tasks=[[list(k), v] for k, v in pi.items()], fleet=nu) for s, (pi, nu) in prices.items()},
                assignment_duals=gamma, pricing=records, pricing_lower_bounds=rho,
                pricing_complete=complete, built_sites=list(pricers), build_runtime=build,
                pricing_runtime=time.perf_counter()-begun-build, old_rho_reused=False,
                balanced=balanced, **explicit_dual_bound(prices, gamma, counts, rho))
