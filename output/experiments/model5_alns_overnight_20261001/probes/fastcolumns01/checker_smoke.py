"""Full model replay and explicit unvisited-category lower-bound checks."""
import json, math, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'candidate_source'))
sys.path.insert(1, str(HERE)); sys.path.insert(2, str(HERE.parents[4]))
from tests.routing.test_multibatch_hybrid import capacity_problem
from tests.routing.test_multibatch_route_cg import enumerate_trips
from models.routing.solvers.exact.multibatch_route_pricing import route_context, RoutePricer
from models.routing.solvers.exact.multibatch_route_cg import site_route_cover, dual_certificate
from models.routing.solvers.exact.multibatch_capacity_dual_repair import repair_capacity_duals
import validation_capacity as capacity
import validation_conjunction as joint

p = capacity_problem(); context = route_context(p, 'S')
path = HERE / 'checker_smoke_models/column_price.mps'
row = site_route_cover(p, context, deadline=time.perf_counter()+3, heuristic_pricing=False,
    batch_pricing=False, delivery_count_cuts=True, dual_repair=True, count_dual_repair=True,
    capacity_dual_repair=True, subset_cuts=[[('A', 'd'), ('B', 'd')], [('B', 'd'), ('C', 'd')]],
    pricing_export_path=path)
assert row['lp_converged'] and abs(row['bound']-4) < 1e-5
capacity.verify_capacity_site(p, dict(site=row), context)
joint.verify_pricing_models(p, row, context, path)

subsets = {i: frozenset((oid, 'd') for oid in pair)
           for i, pair in enumerate([['A', 'B'], ['B', 'C'], ['A', 'C']])}
pi = {n['key']: 10. if n['key'][1] == 'd' else 2. for n in context['nodes']}
prices = {i: 1. for i in subsets}; nu = 0.
pricer = RoutePricer(p, context, subset_keys=subsets)
try:
    priced = pricer.price(pi, nu, time_limit=3, stop_at_negative=True,
        category_duals={'b1': 0.}, subset_duals=prices, reuse_budget=True)
    assert priced['reduced_cost'] < -1e-6
    assert {g['category'] for g in priced['groups']} == {None, 'b1'}
    unvisited = [g for g in priced['groups'] if g['runtime'] == 0 and not g['optimal']]
    assert unvisited and not priced['optimal']
    oracle = enumerate_trips(p, 'S'); checks = 0
    for group in priced['groups']:
        for tasks, cost in oracle:
            category = 'b1' if any(kind == 'd' for _, kind in tasks) else None
            if category != group['category']:
                continue
            reduced = cost-math.fsum(pi[k] for k in tasks)-nu
            reduced -= math.fsum(prices[sid] for sid, keys in subsets.items() if keys.intersection(tasks))
            assert group['lower_bound'] <= reduced + 1e-7
            checks += 1
    repaired = repair_capacity_duals(context, pi, nu, priced, {'b1': (2, 0.)},
        {sid: (keys, 1, 1.) for sid, keys in subsets.items()})
    repaired['bound'] += 3.
    standard = dual_certificate(pi, nu, context['maximum_trips'], priced['lower_bound']) + 3.
    witness = dict(phase='distance', method='integer_pricing', master_objective=4.,
        duals=[[list(k), v] for k, v in pi.items()], fleet_dual=nu, maximum_trips=context['maximum_trips'],
        pricing={k: v for k, v in priced.items() if k not in ('column', 'columns')},
        new_column=priced['column'], delivery_counts=[['b1', 2, 0.]],
        subset_counts=[[sid, [list(k) for k in sorted(keys)], 1, 1.] for sid, keys in subsets.items()],
        standard_candidate_bound=standard, candidate_bound=max(standard, repaired['bound']), dual_repair=repaired)
    row = dict(satellite='S', bound=max(0., witness['candidate_bound']), status='DIAGNOSTIC_EARLY_COLUMN',
        lp_converged=False, best_witness=0, iterations=[witness], columns=priced['columns'])
    path = HERE / 'checker_smoke_models/early_price.mps'; pricer.model.write(str(path))
    capacity.verify_capacity_site(p, dict(site=row), context)
    joint.verify_pricing_models(p, row, context, path)
finally:
    pricer.dispose()
(HERE / 'checker_smoke.json').write_text(json.dumps(dict(status='VERIFIED', complete_cover_bound=4.,
    explicit_early_stop=True, unvisited_category_count=len(unvisited), all_route_bound_checks=checks,
    early_stop_bound=row['bound'], repair_checks=capacity.repair_checks, model_checks=len(joint.models)), indent=2) + '\n')
print('VERIFIED EARLY COLUMN COMPLETE-DOMAIN SMOKE', flush=True)
