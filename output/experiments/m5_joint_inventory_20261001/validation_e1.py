"""Independent E1 route oracle and exported-MPS/projection replay."""
from itertools import permutations
import math

from models.routing.common.cycle.audit import audit_solution


ORACLE = {}


def all_patterns(problem):
    if problem in ORACLE:
        return ORACLE[problem]
    p, f, rows = problem.operating_policy, problem.fleet, []
    for b in p.batches:
        ds = [s for s in problem.satellites if problem.demands[s, b.id]]
        ps = [s for s in problem.satellites if any(o.satellite == s for o in problem.orders)]
        deliveries = [seq for n in range(len(ds)+1) for seq in permutations(ds, n)]
        pickups = [seq for n in range(len(ps)+1) for seq in permutations(ps, n)]
        for delivery in deliveries:
            for pickup in pickups:
                if not delivery and not pickup:
                    continue
                if (sum(problem.weights[s, b.id] for s in delivery) > f.e1_weight_cap+1e-6
                        or sum(problem.demands[s, b.id]*f.container_volume for s in delivery) > f.e1_volume_cap+1e-6
                        or len(pickup)*f.container_weight > f.e1_weight_cap+1e-6
                        or len(pickup)*f.container_volume > f.e1_volume_cap+1e-6):
                    continue
                now = b.start+p.e1_load_time if delivery else b.cutoff
                previous, cost, return_leg, valid = problem.depot, 0, False, True
                for site, kind in [(s, 'd') for s in delivery]+[(s, 'p') for s in pickup]:
                    if kind == 'p' and not return_leg:
                        now = max(now, b.cutoff)
                        return_leg = True
                    cost += problem.distance(previous, site)
                    now += problem.travel(previous, site)+problem.service(site)
                    previous = site
                    if kind == 'd' and now > b.cutoff:
                        valid = False
                        break
                end = now+problem.travel(previous, problem.depot)+p.depot_return_unload_time
                if (not valid or end > b.end or end+p.e1_turnaround_time > p.cycle_end
                        or pickup and end+p.cleaning_duration > p.cycle_end):
                    continue
                rows.append(dict(batch=b.id, delivery=delivery, pickup=pickup,
                                 cost=cost+problem.distance(previous, problem.depot), end=end))
    ORACLE[problem] = rows
    return rows


def verify_e1(problem, initial, cert, mps):
    import gurobipy as gp
    from gurobipy import GRB
    audit = audit_solution(problem, initial)
    assert audit['valid']
    assert not cert['proven_optimal'] and not cert['proven_infeasible']
    assert cert['bound'] <= audit['distances']['e1']+1e-6
    assert cert['raw_bound'] is not None
    assert math.isclose(cert['bound'], max(0, cert['raw_bound']-cert['margin']), abs_tol=1e-6)
    assert mps.exists()
    model = gp.read(str(mps))
    try:
        assert model.NumVars == cert['variables'] and model.NumConstrs == cert['constraints']
        model.Params.OutputFlag = 0
        model.Params.Threads = 1
        model.Params.MIPGap = model.Params.MIPGapAbs = 0
        model.Params.Seed = 42
        model.Params.TimeLimit = 30 if cert['backend'] == 'indexed' else 5
        model.optimize()
        assert model.ObjBound >= cert['raw_bound']-1e-5
        if cert['relaxation_optimal']:
            assert model.Status == GRB.OPTIMAL
            assert math.isclose(model.ObjVal, cert['raw_bound'], rel_tol=1e-9, abs_tol=1e-5)
        proof = dict(path=str(mps), backend=cert['backend'], bound=cert['bound'],
                     reoptimized_bound=model.ObjBound, reoptimized_status=int(model.Status))
        if cert['backend'] == 'patterns':
            patterns = all_patterns(problem)
            assert cert['enumeration_complete'] and len(patterns) == cert['route_columns']
            lookup = {(r['batch'], r['delivery'], r['pickup']): i for i, r in enumerate(patterns)}
            selected, amounts = set(), {}
            for trip in initial['e1_trips']:
                d = tuple(e['site_id'] for e in trip['events'] if e['kind'] == 'd')
                p = tuple(e['site_id'] for e in trip['events'] if e['kind'] == 'p')
                i = lookup[trip['batch_id'], d, p]
                assert patterns[i]['end'] <= trip['return_complete']
                selected.add(i)
                amounts.update({(i, e['site_id']): e['quantity'] for e in trip['events'] if e['kind'] == 'p'})
            def fix(name, value):
                var = model.getVarByName(name)
                assert var is not None, name
                var.LB = var.UB = value
            for i, r in enumerate(patterns):
                route = model.getVarByName(f'route[{i}]')
                assert route is not None and math.isclose(route.Obj, r['cost'], rel_tol=1e-9, abs_tol=1e-6)
                fix(f'route[{i}]', int(i in selected))
                for site in r['pickup']:
                    fix(f'quantity_{i}_{site}', amounts.get((i, site), 0))
            returned = {e['order_id']: t['return_complete'] for t in initial['e2_trips']
                        for e in t['events'] if e['kind'] == 'p'}
            for oid, value in returned.items():
                fix('e2_return_'+oid, value)
                first = next(b.id for b in problem.operating_policy.batches if value <= b.cutoff)
                for b in problem.operating_policy.batches:
                    fix(f'cutoff_{oid}_{b.id}', int(first == b.id))
            model.optimize()
            assert model.Status == GRB.OPTIMAL
            assert math.isclose(model.ObjVal, audit['distances']['e1'], rel_tol=1e-9, abs_tol=1e-5)
            proof.update(complete_routes=len(patterns), full_schedule_projection=True)
        return proof
    finally:
        model.dispose()
