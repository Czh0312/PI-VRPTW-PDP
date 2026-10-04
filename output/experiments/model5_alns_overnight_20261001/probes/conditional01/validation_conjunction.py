from validation_core import *
import gurobipy as gp

def check_joint_conjunction(problem, result, reference, cert, path, old_path):
    import gurobipy as gp
    model, old = gp.read(str(path)), gp.read(str(old_path))
    try:
        patterns = all_patterns(problem)
        assert len(patterns) == cert['patterns']
        original_vars = {v.VarName: v for v in old.getVars()}
        variables = {v.VarName: v for v in model.getVars()}
        flags = {r['activation_variable'] for r in cert['conditional_rows'] if 'activation_variable' in r}
        assert set(variables)-set(original_vars) == {f'conditional_E2_cost[{s}]' for s in problem.satellites}|flags
        for flag in flags:
            assert variables[flag].VType == 'B' and variables[flag].LB == 0 and variables[flag].UB == 1
            close(variables[flag].Obj, 0)
        for name, var in original_vars.items():
            other = variables[name]
            assert var.VType == other.VType
            close(var.LB, other.LB); close(var.UB, other.UB); close(var.Obj, other.Obj)
        floor_count = sum(r['echelon'] == 'E1' for r in reference['distance_certificate']['components'])
        prefix = old.NumConstrs-floor_count
        extra_rows = sum(len(r['supply_conditions'])+1 for r in cert['conditional_rows'] if 'supply_conditions' in r)
        assert model.NumConstrs == prefix+1+len(problem.satellites)+len(cert['conditional_rows'])+extra_rows
        for a, b in zip(old.getConstrs()[:prefix], model.getConstrs()[:prefix]):
            assert a.Sense == b.Sense
            close(a.RHS, b.RHS); same_expr(old.getRow(a), model.getRow(b))
        assert old.NumGenConstrs == model.NumGenConstrs
        for a, b in zip(old.getGenConstrs(), model.getGenConstrs()):
            av, al, ae, ass, ar = old.getGenConstrIndicator(a)
            bv, bl, be, bs, br = model.getGenConstrIndicator(b)
            assert av.VarName == bv.VarName and al == bl and ass == bs
            close(ar, br); same_expr(ae, be)
        floors = {r['satellite']: r['bound'] for r in reference['components']}
        e1_floor = model.getConstrByName('certified_E1_cost')
        assert e1_floor.Sense == '>'
        close(e1_floor.RHS, reference['e1_bound'])
        observed = expr(model.getRow(e1_floor))
        expected_cost = {v.VarName: v.Obj for v in old.getVars() if v.Obj}
        assert set(observed) == set(expected_cost)
        for name, value in expected_cost.items(): close(observed[name], value)
        for site, floor in floors.items():
            variable = variables[f'conditional_E2_cost[{site}]']
            close(variable.Obj, 1); close(variable.LB, 0)
            con = model.getConstrByName('unconditional_E2_'+site)
            close(con.RHS, floor)
            assert con.Sense == '>' and expr(model.getRow(con)) == {variable.VarName: 1}
        expected = []
        for row in cert['conditional_rows']:
            close(row['base_bound'], floors[row['site']])
            delta = row['bound']-row['base_bound']
            def indices(batch, threshold):
                late = []
                for i, pattern in enumerate(patterns):
                    if pattern['batch'] != batch: continue
                    clock = problem.batch_map[batch].start+problem.operating_policy.e1_load_time
                    previous = problem.depot
                    for site in pattern['delivery']:
                        clock += problem.travel(previous, site)+problem.service(site)
                        previous = site
                        if site == row['site'] and clock >= threshold: late.append(i)
                return late
            coefficients = {f'conditional_E2_cost[{row["site"]}]': 1}
            if 'supply_conditions' in row:
                groups = [indices(c['batch'], c['threshold']) for c in row['supply_conditions']]
                assert groups == row['late_indices_by_batch']
                flag = row['activation_variable']
                coefficients[flag] = -delta
                observed = [(c.Sense, c.RHS, expr(model.getRow(c))) for c in model.getConstrs()
                            if flag in expr(model.getRow(c)) and not c.ConstrName.startswith('conditional_floor_')]
                assert len(observed) == len(groups)+1
                for group in groups:
                    terms = {flag: 1};terms.update({f'route[{i}]': -1 for i in group})
                    assert ('<', 0, terms) in observed
                terms = {flag: 1}
                for group in groups:
                    for i in group: terms[f'route[{i}]'] = terms.get(f'route[{i}]', 0)-1
                assert ('>', 1-len(groups), terms) in observed
            else:
                late = indices(row['batch'], row['threshold'])
                assert late == row['late_indices']
                coefficients.update({f'route[{i}]': -delta for i in late})
            expected.append((row['base_bound'], coefficients))
        actual = [c for c in model.getConstrs() if c.ConstrName.startswith('conditional_floor_')]
        assert len(actual) == len(expected)
        for con, (rhs, coefficients) in zip(actual, expected):
            assert con.Sense == '>'
            close(con.RHS, rhs)
            observed = expr(model.getRow(con))
            assert set(observed) == set(coefficients)
            for name, value in coefficients.items(): close(observed[name], value)
        model.Params.OutputFlag = 0; model.Params.Threads = 1; model.Params.Seed = 43
        model.Params.MIPGap = model.Params.MIPGapAbs = 0; model.Params.TimeLimit = 30
        model.optimize()
        assert model.ObjBound >= cert['raw_bound']-1e-5
        reoptimized = model.ObjBound
        def fix(name, value):
            variable = variables[name]; variable.LB = variable.UB = value
        lookup = {(r['batch'], r['delivery'], r['pickup']): i for i, r in enumerate(patterns)}
        selected, quantities = set(), {}
        for trip in result['e1_trips']:
            delivery = tuple(e['site_id'] for e in trip['events'] if e['kind'] == 'd')
            pickup = tuple(e['site_id'] for e in trip['events'] if e['kind'] == 'p')
            i = lookup[trip['batch_id'], delivery, pickup]; selected.add(i)
            quantities.update({(i, e['site_id']): e['quantity'] for e in trip['events'] if e['kind'] == 'p'})
        for i, pattern in enumerate(patterns):
            close(variables[f'route[{i}]'].Obj, pattern['cost'])
            fix(f'route[{i}]', int(i in selected))
            for site in pattern['pickup']: fix(f'quantity_{i}_{site}', quantities.get((i, site), 0))
        costs = {s: 0.0 for s in problem.satellites}
        for trip in result['e2_trips']:
            site = trip['satellite_id']
            costs[site] += trip_cost(problem, site, [(e['order_id'], e['kind']) for e in trip['events']])
            for event in trip['events']:
                oid, kind = event['order_id'], event['kind']
                fix(('delivery_' if kind == 'd' else 'pickup_time_')+oid, event['start'])
                if kind == 'p':
                    fix('e2_return_'+oid, trip['return_complete'])
                    first = next(b.id for b in problem.operating_policy.batches if trip['return_complete'] <= b.cutoff)
                    for batch in problem.operating_policy.batches: fix(f'cutoff_{oid}_{batch.id}', int(first == batch.id))
        for site, cost in costs.items(): fix(f'conditional_E2_cost[{site}]', cost)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        close(model.ObjVal, checked_audit(problem, result)['objective'])
        return dict(path=str(path), reoptimized_bound=reoptimized, full_schedule_projection=True,
                    projected_objective=model.ObjVal, patterns=len(patterns), compared_linear_rows=prefix)
    finally:
        model.dispose(); old.dispose()



import validation_core as check
from models.routing.solvers.exact.multibatch_route_pricing import RoutePricer
models=[]

def verify_pricing_models(p,row,context,path):
    if row['best_witness'] is None:return
    witness=row['iterations'][row['best_witness']]
    subset={sid:frozenset(tuple(k) for k in keys) for sid,keys,_,_ in witness.get('subset_counts',[])}
    built=RoutePricer(p,context,subset_keys=subset)
    pi={tuple(k):v for k,v in witness['duals']}
    nu=witness['fleet_dual'];subset_prices={sid:v for sid,_,_,v in witness.get('subset_counts',[])}
    prices={bid:v for bid,_,v in witness.get('delivery_counts',[])}
    try:
        for number,(category,pricer) in enumerate(built.groups):
            pricer.set_prices(pi,nu,False,subset_prices);pricer.model.update()
            file=path.with_name(path.stem+f'_group_{number}'+path.suffix)
            model=gp.read(str(file)); original=pricer.model
            try:
                assert model.NumVars==original.NumVars and model.NumConstrs==original.NumConstrs
                assert model.NumGenConstrs==original.NumGenConstrs
                for a,b in zip(original.getVars(),model.getVars()):
                    assert a.VarName==b.VarName and a.VType==b.VType
                    check.close(a.LB,b.LB);check.close(a.UB,b.UB);check.close(a.Obj,b.Obj)
                check.close(original.ObjCon,model.ObjCon)
                for a,b in zip(original.getConstrs(),model.getConstrs()):
                    assert a.Sense==b.Sense
                    check.close(a.RHS,b.RHS);check.same_expr(original.getRow(a),model.getRow(b))
                for a,b in zip(original.getGenConstrs(),model.getGenConstrs()):
                    av,al,ae,ass,ar=original.getGenConstrIndicator(a)
                    bv,bl,be,bs,br=model.getGenConstrIndicator(b)
                    assert av.VarName==bv.VarName and al==bl and ass==bs
                    check.close(ar,br);check.same_expr(ae,be)
                model.Params.OutputFlag=0;model.Params.Threads=1;model.Params.Seed=43
                model.Params.MIPGap=model.Params.MIPGapAbs=0;model.Params.TimeLimit=30
                model.Params.FeasibilityTol=model.Params.IntFeasTol=1e-9
                model.Params.IntegralityFocus=1;model.Params.DualReductions=0
                model.optimize()
                report=next(g for g in witness['pricing']['groups'] if g['category']==category)
                if report['infeasible']: assert model.Status==gp.GRB.INFEASIBLE
                elif report['raw_lower_bound'] is not None:
                    assert model.ObjBound-prices.get(category,0)>=report['raw_lower_bound']-1e-5
                models.append(dict(path=str(file),full_domain_matrix_equal=True,category=category,
                    reoptimized_bound=model.ObjBound-prices.get(category,0) if not report['infeasible'] else None))
            finally:model.dispose()
    finally:built.dispose()
