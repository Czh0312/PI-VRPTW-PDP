"""Check full binary/LP matrix equivalence and replay the integer optimum."""
from collections import Counter
import gzip
import json
import math


def close(a, b):
    assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6), (a, b)


def verify_integer(problem, initial, site, universe_path, lp_path, integer_path, replay_column):
    import gurobipy as gp
    from gurobipy import GRB
    cert = site['integer_cover']
    assert cert['scope'] == 'M5_relaxed_integer_E2_route_cover'
    assert cert['complete_route_universe'] and cert['relaxation_optimal']
    assert not cert['proven_optimal'] and not cert['proven_infeasible']
    assert not cert['relaxation_infeasible']
    close(cert['lp_bound'], site['lp_bound'])
    close(cert['margin'], 1e-7*max(1, abs(cert['raw_bound'])))
    close(cert['bound'], max(site['lp_bound'], max(0, cert['raw_bound']-cert['margin'])))
    close(site['bound'], cert['bound'])
    with gzip.open(universe_path, 'rt') as stream:
        universe = json.load(stream)
    assert universe['complete'] and all(g['complete'] for g in universe['groups'])
    all_columns = [c for g in universe['groups'] for c in g['columns']]
    lowest = {frozenset(tuple(k) for k in c['tasks']): c['cost'] for c in all_columns}
    assert len(lowest) == len(all_columns) == cert['variables']
    required = {(o.id, kind) for o in problem.orders if o.satellite == site['satellite'] for kind in ('d', 'p')}
    projected = []
    for trip in initial['e2_trips']:
        if trip['satellite_id'] != site['satellite']:
            continue
        tasks = tuple((e['order_id'], e['kind']) for e in trip['events'])
        task_set = frozenset(tasks)
        assert task_set in lowest
        places = [site['satellite']]+[problem.order_map[oid].customer for oid, _ in tasks]+[site['satellite']]
        original_cost = math.fsum(problem.distance(a, b) for a, b in zip(places, places[1:]))
        assert lowest[task_set] <= original_cost+1e-6
        projected.append(task_set)
    assert Counter(k for tasks in projected for k in tasks) == Counter({k: 1 for k in required})
    assert len(projected) <= cert['maximum_trips']
    close(cert['initial_projection']['objective'], math.fsum(lowest[t] for t in projected))
    assert cert['initial_projection']['mapped_trips'] == len(projected)
    cover, objective = Counter(), 0.0
    for column in cert['selected_columns']:
        replay_column(problem, site['satellite'], column)
        close(column['integer_value'], 1)
        tasks = frozenset(tuple(k) for k in column['tasks'])
        close(column['cost'], lowest[tasks])
        cover.update(tasks)
        objective += column['cost']
    assert cover == Counter({k: 1 for k in required})
    assert len(cert['selected_columns']) <= cert['maximum_trips']
    close(objective, cert['relaxed_objective'])
    close(objective, cert['raw_bound'])
    lp, integer = gp.read(str(lp_path)), gp.read(str(integer_path))
    try:
        assert lp.NumVars == integer.NumVars == cert['variables']
        assert lp.NumConstrs == integer.NumConstrs == cert['constraints']
        for before, after in zip(lp.getVars(), integer.getVars()):
            assert before.VarName == after.VarName and before.VType == GRB.CONTINUOUS
            assert after.VType == GRB.BINARY and after.LB == 0 and after.UB == 1
            close(before.Obj, after.Obj)
        for before, after in zip(lp.getConstrs(), integer.getConstrs()):
            assert before.ConstrName == after.ConstrName and before.Sense == after.Sense
            close(before.RHS, after.RHS)
            a, b = lp.getRow(before), integer.getRow(after)
            assert a.size() == b.size()
            original = {a.getVar(j).index: a.getCoeff(j) for j in range(a.size())}
            changed = {b.getVar(j).index: b.getCoeff(j) for j in range(b.size())}
            assert original == changed
        integer.Params.OutputFlag = 0
        integer.Params.Threads = 1
        integer.Params.MIPGap = integer.Params.MIPGapAbs = 0
        integer.Params.IntFeasTol = integer.Params.FeasibilityTol = 1e-9
        integer.Params.TimeLimit = 30
        integer.optimize()
        assert integer.Status == GRB.OPTIMAL
        close(integer.ObjVal, cert['raw_bound'])
        close(integer.ObjBound, cert['raw_bound'])
        # Rebuild coverage from solver values, not production selected columns.
        rebuilt, cost, trips = Counter(), 0.0, 0
        for var, column in zip(integer.getVars(), all_columns):
            if var.X > .5:
                replay_column(problem, site['satellite'], column)
                rebuilt.update(tuple(k) for k in column['tasks'])
                cost += column['cost']
                trips += 1
        assert rebuilt == Counter({k: 1 for k in required}) and trips <= cert['maximum_trips']
        close(cost, integer.ObjVal)
        return dict(mps=str(integer_path), full_columns=len(all_columns), full_matrix_equal_to_lp=True,
                    reoptimized_integer_objective=integer.ObjVal, reconstructed_cost=cost,
                    reconstructed_trips=trips, full_schedule_projection=True,
                    limitation='Integer relaxed cover is not a full-M5 feasible schedule.')
    finally:
        lp.dispose()
        integer.dispose()
