"""Experimental audited starts for M1--M4; never changes solver defaults.

Only Start attributes are populated. Auxiliary continuous loads and causal
ranks are left to Gurobi, and no bounds or constraints are changed.
"""
from collections import defaultdict
from copy import deepcopy

from models.common.cycle.assignment import input_assignment
from models.common.cycle.audit import audit_solution


def canonical_start(problem, candidate):
    """Validate the physical plan and relabel interchangeable vehicle slots."""
    if problem.model_number not in (1, 2, 3, 4) or len(problem.operating_policy.batches) != 1:
        raise ValueError('Experimental starts require M1--M4 with B=1')
    if getattr(problem, 'cost_objective', False) or candidate.get('objective_mode', 'distance') != 'distance':
        raise ValueError('This experiment supports distance objectives only')
    source = candidate.get('model')
    if source != problem.model_number and (source, problem.model_number) not in ((1, 2), (3, 4)):
        raise ValueError('Only same-model or M1->M2 / M3->M4 starts are supported')
    result = deepcopy(candidate)
    result['model'] = problem.model_number
    if problem.free_assignment and source != problem.model_number:
        result['assignment'] = input_assignment(problem)
    result['info'] = dict(result['info'], status='INITIAL_FEASIBLE', proven_optimal=False, proven_infeasible=False,
                          best_bound=None, mip_gap=None, proof_scope='audited_initial_plan_only')
    result.pop('audit', None)
    audit = audit_solution(problem, result)
    if not audit['valid']:
        raise ValueError(f'Invalid initial plan: {audit["errors"]}')
    first = defaultdict(list)
    for trip in result['e1_trips']:
        first[trip['batch_id']].append(trip)
    for batch, trips in first.items():
        for vehicle, trip in enumerate(sorted(trips, key=lambda t: t['vehicle_id'])):
            trip.update(vehicle_id=vehicle, trip_id=f'start_e1_{batch}_{vehicle}')
    second = defaultdict(lambda: defaultdict(list))
    for trip in result['e2_trips']:
        second[trip['satellite_id']][trip['vehicle_id']].append(trip)
    for site, vehicles in second.items():
        # The MILP orders homogeneous vehicles by nonincreasing trip count.
        ordered = sorted(vehicles.items(), key=lambda item: (-len(item[1]), item[0]))
        for vehicle, (_, trips) in enumerate(ordered):
            for slot, trip in enumerate(sorted(trips, key=lambda t: (t['departure'], t['trip_index']))):
                trip.update(vehicle_id=vehicle, trip_index=slot,
                            trip_id=f'start_e2_{site}_{vehicle}_{slot}')
    audit = audit_solution(problem, result)
    if not audit['valid']:
        raise ValueError(f'Invalid relabeled initial plan: {audit["errors"]}')
    return result, audit


def apply_mip_start(problem, model, context, candidate):
    """Map a complete audited physical plan to a partial Gurobi MIP start."""
    from gurobipy import GRB

    initial, audit = canonical_start(problem, candidate)
    model.update()
    values, handles, zero, selected = {}, {}, set(), set()

    def value(variable, number):
        if not hasattr(variable, 'index'):
            return
        index = variable.index
        if index in values and abs(values[index] - number) > 1e-7:
            raise ValueError(f'Conflicting initial values for {variable.VarName}')
        handles[index], values[index] = variable, number

    def binary(variable, active):
        if not hasattr(variable, 'index'):
            return  # Derived expressions are determined by their operands.
        handles[variable.index] = variable
        zero.add(variable.index)
        if active:
            selected.add(variable.index)

    plans = {
        'e1': {(t['batch_id'], t['vehicle_id']): t for t in initial['e1_trips']},
        'e2': {(t['satellite_id'], t['vehicle_id'], t['trip_index']): t for t in initial['e2_trips']},
    }
    used = {'e1': set(), 'e2': set()}
    task_edges = set()
    for layer in ('e1', 'e2'):
        for route in context[layer]:
            key = ((route['batch'].id, route['vehicle']) if layer == 'e1' else
                   (route['satellite'], route['vehicle'], route['slot']))
            trip = plans[layer].get(key)
            binary(route['used'], trip is not None)
            lookup = {((n['site'] if layer == 'e1' else n['order'].id), n['kind']): i
                      for i, n in enumerate(route['nodes'])}
            order = []
            if trip is not None:
                used[layer].add(key)
                for event in trip['events']:
                    event_key = (event['site_id' if layer == 'e1' else 'order_id'], event['kind'])
                    if event_key not in lookup:
                        raise ValueError(f'Initial task absent from route domain: {event_key}')
                    index = lookup[event_key]
                    order.append(index)
                    value(route['times'][index], event['start'])
                    if layer == 'e1':
                        value(route['nodes'][index]['quantity'], event['quantity'])
                value(route['start'], trip['departure'])
                value(route['end'], trip['return_complete'])
            else:
                value(route['start'], 0)
                value(route['end'], 0)
            chosen = set(zip([-1, *order], [*order, -2])) if order else set()
            if not chosen.issubset(route['x']):
                raise ValueError(f'Initial arcs absent from route domain: {chosen-set(route["x"])}')
            for index, variable in route['visit'].items():
                binary(variable, index in order)
            for edge, variable in route['x'].items():
                # Internal compressed arcs are shared across ALL route slots.
                binary(variable, edge in chosen)
                if layer == 'e2' and edge in chosen and min(edge) >= 0:
                    left, right = (route['nodes'][i] for i in edge)
                    task_edges.add(((left['order'].id, left['kind']), (right['order'].id, right['kind'])))
            if 'has_delivery' in route:
                binary(route['has_delivery'], any(route['nodes'][i]['kind'] == 'd' for i in order))
    if any(used[layer] != set(plans[layer]) for layer in plans):
        raise ValueError('Initial plan exceeds the MILP vehicle/trip slot domain')
    variables = context['variables']
    assignment = initial.get('assignment', input_assignment(problem))
    for (customer, site), variable in variables.assignments.items():
        binary(variable, assignment[customer] == site)
    for site, variable in variables.active_sites.items():
        binary(variable, site in assignment.values())
    for edge, variable in variables.aggregate_arcs.items():
        binary(variable, edge in task_edges)
    for index in zero:
        value(handles[index], int(index in selected))
    # Never inherit a prior solve's bound or proof, or fix the assignment.
    for variable in model.getVars():
        variable.Start = GRB.UNDEFINED
    for index, number in values.items():
        handles[index].Start = number
    return dict(objective=audit['objective'], variables_populated=len(values),
                binary_variables_populated=len(zero), physical_audit=audit,
                canonical_result=initial, start_scope='audited_routes_and_integer_times',
                bounds_or_constraints_changed=False)
