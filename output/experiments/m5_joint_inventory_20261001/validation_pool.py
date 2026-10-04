"""Reoptimize an exported pool MPS and rebuild the whole schedule independently."""
import json
import math
from copy import deepcopy

from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.state import empty_result
from validation_e1 import all_patterns


def replay_pool(problem, directory):
    import gurobipy as gp
    from gurobipy import GRB
    initial = json.loads((directory/'initial_result.json').read_text())
    output = json.loads((directory/'output_result.json').read_text())
    columns = json.loads((directory/'columns.json').read_text())
    assert audit_solution(problem, initial)['valid'] and audit_solution(problem, output)['valid']
    assert output['info']['route_pool_backend'] == 'patterns'
    assert not output['info']['proven_optimal'] and not output['info']['proven_infeasible']
    assert output['info']['best_bound'] is None and output['info']['mip_gap'] is None
    model = gp.read(str(directory/'route_pool.mps'))
    try:
        model.Params.OutputFlag = 0
        model.Params.Threads = 1
        model.Params.MIPGap = model.Params.MIPGapAbs = 0
        model.Params.Seed = 42
        model.Params.TimeLimit = 10
        model.optimize()
        assert model.Status == GRB.OPTIMAL
        assert output['info']['route_pool_subproblem_optimal']
        assert math.isclose(model.ObjVal, output['info']['route_pool_subproblem_bound'], rel_tol=1e-9, abs_tol=1e-5)
        assert model.NumVars == output['info']['route_pool_variables']
        rebuilt = empty_result(problem, 'independent_route_pool_MPS_replay')
        for i, (trip, rows) in enumerate(zip(initial['e2_trips'], columns)):
            selected = []
            for j, column in enumerate(rows):
                var = model.getVarByName(f'pool_choice_{i}[{j}]')
                assert var is not None and math.isclose(var.Obj, column['cost'], abs_tol=1e-6)
                if var.X > .5:
                    selected.append(column)
            assert len(selected) <= 1
            if not selected:
                continue
            events = [dict(order_id=oid, kind=kind, quantity=problem.order_map[oid].count,
                start=int(round(model.getVarByName(('delivery_' if kind == 'd' else 'pickup_time_')+oid).X)))
                for oid, kind in selected[0]['tasks']]
            rebuilt['e2_trips'].append(dict(trip_id=trip['trip_id'], satellite_id=trip['satellite_id'],
                vehicle_id=trip['vehicle_id'], trip_index=trip['trip_index'], events=events,
                departure=int(round(model.getVarByName(f'pool_start_{i}').X)),
                return_complete=int(round(model.getVarByName(f'pool_end_{i}').X))))
        next_vehicle = {b.id: 0 for b in problem.operating_policy.batches}
        occupied = {t['trip_id'] for t in rebuilt['e2_trips']}
        for i, pattern in enumerate(all_patterns(problem)):
            var = model.getVarByName(f'route[{i}]')
            assert var is not None and math.isclose(var.Obj, pattern['cost'], abs_tol=1e-6)
            if var.X < .5:
                continue
            b = problem.batch_map[pattern['batch']]
            now = b.start+problem.operating_policy.e1_load_time if pattern['delivery'] else b.cutoff
            departure, previous, events = now, problem.depot, []
            for site in pattern['delivery']:
                now += problem.travel(previous, site)
                events.append(dict(kind='d', site_id=site, quantity=problem.demands[site, b.id], start=now))
                now += problem.service(site)
                previous = site
            if pattern['pickup']:
                now = max(now, b.cutoff)
            for site in pattern['pickup']:
                now += problem.travel(previous, site)
                quantity = int(round(model.getVarByName(f'quantity_{i}_{site}').X))
                events.append(dict(kind='p', site_id=site, quantity=quantity, start=now))
                now += problem.service(site)
                previous = site
            end = now+problem.travel(previous, problem.depot)+problem.operating_policy.depot_return_unload_time
            assert end == pattern['end']
            vehicle = next_vehicle[b.id]
            next_vehicle[b.id] += 1
            trip_id = f'independent_e1_{b.id}_{vehicle}'
            while trip_id in occupied:
                trip_id += '_'
            occupied.add(trip_id)
            rebuilt['e1_trips'].append(dict(trip_id=trip_id, batch_id=b.id, vehicle_id=vehicle,
                departure=departure, return_complete=end, events=events))
        audit = audit_solution(problem, rebuilt)
        assert audit['valid'] and audit['periodic_replay']['valid'], audit['errors']
        assert math.isclose(audit['objective'], model.ObjVal, rel_tol=1e-9, abs_tol=1e-5)
        assert math.isclose(audit['objective'], output['info']['objective'], rel_tol=1e-9, abs_tol=1e-5)
        return dict(path=str(directory/'route_pool.mps'), optimal_objective=model.ObjVal,
                    reconstructed_objective=audit['objective'], full_cycle_valid=True,
                    columns=sum(map(len, columns)), active_E2_trips=len(rebuilt['e2_trips']))
    finally:
        model.dispose()
