"""Read the restricted MPS and independently rebuild a complete M5 schedule."""
from copy import deepcopy
import math

from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.state import empty_result
from validation_e1 import all_patterns


def replay_polish(problem, initial, result, mps, retimed):
    import gurobipy as gp
    from gurobipy import GRB
    model = gp.read(str(mps))
    try:
        model.Params.OutputFlag = 0
        model.Params.Threads = 1
        model.Params.MIPGap = model.Params.MIPGapAbs = 0
        model.Params.Seed = 42
        model.Params.TimeLimit = 5
        model.optimize()
        assert model.Status == GRB.OPTIMAL
        assert math.isclose(model.ObjVal, result['info']['e1_subproblem_lower_bound'], rel_tol=1e-9, abs_tol=1e-5)
        assert model.NumVars == result['info']['e1_polish_variables']
        assert result['info']['e1_polish_backend'] == 'patterns'
        rebuilt = empty_result(problem, 'independent_MPS_replay')
        rebuilt['e2_trips'] = deepcopy(initial['e2_trips'])
        if retimed:
            for index, trip in enumerate(rebuilt['e2_trips']):
                trip['departure'] = int(round(model.getVarByName(f'e2_departure_{index}').X))
                trip['return_complete'] = int(round(model.getVarByName(f'e2_end_{index}').X))
                for event in trip['events']:
                    prefix = 'delivery_' if event['kind'] == 'd' else 'pickup_time_'
                    event['start'] = int(round(model.getVarByName(prefix+event['order_id']).X))
        else:
            for trip in initial['e2_trips']:
                for event in trip['events']:
                    if event['kind'] == 'p':
                        var = model.getVarByName('e2_return_'+event['order_id'])
                        assert var.LB == var.UB == trip['return_complete']
                    else:
                        order = problem.order_map[event['order_id']]
                        var = model.getVarByName(f'supply_{order.satellite}_{order.batch}')
                        assert var.UB <= trip['departure']-problem.operating_policy.e2_dispatch_prep_time
        next_vehicle = {b.id: 0 for b in problem.operating_policy.batches}
        for index, pattern in enumerate(all_patterns(problem)):
            chosen = model.getVarByName(f'route[{index}]')
            assert chosen is not None and math.isclose(chosen.Obj, pattern['cost'], abs_tol=1e-6)
            if chosen.X < .5:
                continue
            batch = problem.batch_map[pattern['batch']]
            now = batch.start+problem.operating_policy.e1_load_time if pattern['delivery'] else batch.cutoff
            departure, previous, events = now, problem.depot, []
            for site in pattern['delivery']:
                now += problem.travel(previous, site)
                events.append(dict(kind='d', site_id=site, quantity=problem.demands[site, batch.id], start=now))
                now += problem.service(site)
                previous = site
            if pattern['pickup']:
                now = max(now, batch.cutoff)
            for site in pattern['pickup']:
                now += problem.travel(previous, site)
                amount = int(round(model.getVarByName(f'quantity_{index}_{site}').X))
                events.append(dict(kind='p', site_id=site, quantity=amount, start=now))
                now += problem.service(site)
                previous = site
            end = now+problem.travel(previous, problem.depot)+problem.operating_policy.depot_return_unload_time
            assert end == pattern['end']
            v = next_vehicle[batch.id]
            next_vehicle[batch.id] += 1
            rebuilt['e1_trips'].append(dict(trip_id=f'checked_e1_{batch.id}_{v}', vehicle_id=v,
                batch_id=batch.id, departure=departure, return_complete=end, events=events))
        audit = audit_solution(problem, rebuilt)
        assert audit['valid'] and audit['periodic_replay']['valid'], audit['errors']
        assert math.isclose(audit['distances']['e1'], model.ObjVal, abs_tol=1e-5)
        assert math.isclose(audit['objective'], result['info']['objective'], rel_tol=1e-9, abs_tol=1e-5)
        return dict(path=str(mps), optimal_objective=model.ObjVal, reconstructed_objective=audit['objective'],
                    full_cycle_valid=True, retimed=retimed)
    finally:
        model.dispose()
