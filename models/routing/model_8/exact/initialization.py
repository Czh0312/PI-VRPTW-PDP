"""Replay physical incumbents and encode MILP starts without another algorithm."""
from copy import deepcopy
import math

from models.common.cycle.joint import joint_empty_result, lift_result


def audited_start(problem, supplied):
    if supplied is None:
        return None
    boundaries = supplied.get('batch_boundaries') or (
        [b['start'] for b in supplied.get('candidate_schedule', ())]+[problem.operating_policy.cycle_end])
    selected = (problem.with_boundaries(boundaries)
                if getattr(problem, 'variable_calendar', False) else problem)
    if supplied.get('economic_profile_hash') != problem.cost_profile.hash:
        raise ValueError('Initial solution economic profile differs')
    if supplied.get('candidate_schedule') != joint_empty_result(selected, 'exact')['candidate_schedule']:
        raise ValueError('Initial solution has different candidate starts/ends')
    assignment = dict(supplied['order_batch_assignment'])
    pool = supplied['container_pool_size']
    cutoffs = supplied.get('batch_cutoffs')
    loading = supplied.get('batch_loading_starts')
    fixed = selected.project(assignment, pool, cutoffs, loading)
    from models.common.cycle.audit import _audit
    checked = _audit(fixed, {**supplied, 'model': fixed.model_number,
                            'info': {**supplied['info'], 'objective': None}}, True)
    if not checked['valid']:
        raise ValueError(f'Initial solution fails physical replay: {checked["errors"]}')
    raw = deepcopy(supplied)
    raw['info'] = dict(status='FEASIBLE', objective=None, proven_optimal=False,
                       proven_infeasible=False, best_bound=None, mip_gap=None,
                       initial_source_solver=supplied.get('solver'))
    result = lift_result(selected, raw, assignment, pool, 'exact', cutoffs, loading)
    if not result['audit']['valid']:
        raise ValueError('Initial solution fails joint audit')
    return result


def install_start(model, ctx, result):
    """Partial auxiliary variables are completed by the MILP solver."""
    if result is None:
        return 0
    from gurobipy import Var
    assigned = 0

    def put(variable, value):
        nonlocal assigned
        if isinstance(variable, Var):
            variable.Start = value
            assigned += 1

    put(ctx['pool'], result['container_pool_size'])
    assignment = result['order_batch_assignment']
    for (oid, bid), variable in ctx['assignments'].items():
        put(variable, int(assignment[oid] == bid))
    for variable in ctx['activation'].values():
        put(variable, 1)
    cutoffs = result['batch_cutoffs']
    for bid, variable in ctx['cutoffs'].items():
        put(variable, cutoffs[bid])
    offset = model.getVarByName('shared_cutoff_offset')
    if offset is not None:
        batch = ctx['parameters'].policy.batches[0]
        put(offset, cutoffs[batch.id]-batch.start)
    schedule = {b['id']: b for b in result['candidate_schedule']}
    for bid, variable in ctx['batch_starts'].items():
        put(variable, schedule[bid]['start'])
    for bid, variable in ctx.get('loading_starts', {}).items():
        put(variable, result.get('batch_loading_starts', {}).get(bid, schedule[bid]['start']))

    # Vehicles at a satellite are interchangeable. Canonical numbering respects
    # used-vehicle/slot prefix symmetry without modifying the supplied schedule.
    e2 = {}
    trips = result['e2_trips']
    for site in {t['satellite_id'] for t in trips}:
        used = sorted({t['vehicle_id'] for t in trips if t['satellite_id'] == site})
        for v, old in enumerate(used):
            rows = sorted((t for t in trips if t['satellite_id'] == site and t['vehicle_id'] == old),
                          key=lambda t: (t['departure'], t['trip_index']))
            for slot, trip in enumerate(rows):
                e2[site, v, slot] = trip
    e1 = {(t['batch_id'], t['vehicle_id']): t for t in result['e1_trips']}
    for layer in ('e1', 'e2'):
        for route in ctx[layer]:
            trip = (e1.get((route['batch'].id, route['vehicle'])) if layer == 'e1'
                    else e2.get((route['satellite'], route['vehicle'], route['slot'])))
            if trip is None:
                put(route['used'], 0)
                for variable in route['visit'].values():
                    put(variable, 0)
                for variable in route['x'].values():
                    put(variable, 0)
                continue
            lookup = {(node['kind'], node['site'] if layer == 'e1' else node['order'].id): i
                      for i, node in enumerate(route['nodes'])}
            order = [lookup[(event['kind'], event['site_id' if layer == 'e1' else 'order_id'])]
                     for event in trip['events']]
            path = [-1]+order+[-2]
            arcs = set(zip(path, path[1:]))
            if not arcs.issubset(route['x']):
                # A partial start remains useful when a presolved arc is absent.
                continue
            put(route['used'], 1)
            put(route['start'], trip['departure'])
            put(route['end'], trip['return_complete'])
            for i, variable in route['visit'].items():
                put(variable, int(i in order))
            for arc, variable in route['x'].items():
                put(variable, int(arc in arcs))
            for i, event in zip(order, trip['events']):
                put(route['times'][i], event['start'])
                put(route['nodes'][i]['quantity'], event['quantity'])
                if layer == 'e2':
                    put(ctx['variables'].task_times[event['order_id'], event['kind']], event['start'])
    return assigned


def _assignment_proposals(problem):
    """Deterministic legal batch proposals, without a route-feasibility claim."""
    orders = problem.orders
    batches = problem.operating_policy.batches
    if len(batches) > len(orders):
        return []
    priorities = [
        sorted(orders, key=lambda o: (len(problem.legal_batches[o.id]), -o.count, o.delivery_window[1], o.id)),
        sorted(orders, key=lambda o: (len(problem.legal_batches[o.id]), o.delivery_window[1], o.id)),
    ]
    proposals = []
    for sequence in priorities:
        loads = {b.id: 0 for b in batches}
        assignment = {}
        for index, order in enumerate(sequence):
            choices = list(problem.legal_batches[order.id])
            empty = {bid for bid, value in loads.items() if value == 0}
            if len(empty) == len(orders)-index:
                choices = [bid for bid in choices if bid in empty]
            if not choices:
                break
            bid = min(choices, key=lambda bid: (loads[bid]+order.count,
                      abs(order.delivery_window[0]-problem.batch_map[bid].start), bid))
            assignment[order.id] = bid
            loads[bid] += order.count
        if len(assignment) == len(orders) and all(loads.values()) and assignment not in proposals:
            proposals.append(assignment)
    return proposals


def install_assignment_hints(problem, model, ctx):
    """Give soft guidance without asking the solver to complete a partial start."""
    proposals = _assignment_proposals(problem)
    if not proposals:
        return 0
    for (oid, bid), variable in ctx['assignments'].items():
        values = [int(assignment[oid] == bid) for assignment in proposals]
        variable.VarHintVal = values[0]
        variable.VarHintPri = 2 if len(set(values)) == 1 else 1
    return len(ctx['assignments'])


def install_assignment_guidance(problem, model, ctx):
    """Keep small-model starts; use soft hints with the compact formulation."""
    if ctx['parameters'].compact_e2:
        return 0, install_assignment_hints(problem, model, ctx)
    proposals = _assignment_proposals(problem)
    model.NumStart = len(proposals)
    assigned = 0
    for index, assignment in enumerate(proposals):
        model.Params.StartNumber = index
        ctx['pool'].Start = problem.pool_max
        assigned += 1
        for (oid, bid), variable in ctx['assignments'].items():
            variable.Start = int(assignment[oid] == bid)
            assigned += 1
    if proposals:
        model.Params.StartNumber = 0
    return assigned, 0



def retain_incumbent(result, incumbent, problem, elapsed):
    """A timeout never discards an audited feasible upper bound or invents proof."""
    if incumbent is None:
        return result
    info = result['info']
    if result.get('audit', {}).get('valid') and info['objective'] <= incumbent['info']['objective']+1e-8:
        return result
    if info.get('proven_infeasible'):
        raise RuntimeError('MILP infeasibility conflicts with the audited initial solution')
    from models.common.cycle.pool_bound import necessary_cost_lower_bound
    bound = info.get('best_bound')
    analytical = necessary_cost_lower_bound(problem)
    bound = max(bound, analytical) if bound is not None and math.isfinite(bound) else analytical
    kept = deepcopy(incumbent)
    cost = kept['info']['objective']
    if bound > cost+1e-6+1e-7*abs(cost):
        raise RuntimeError('Lower bound exceeds the audited incumbent')
    bound = min(cost, bound)
    kept['info'].update(info, status='FEASIBLE', objective=cost, best_bound=bound,
                        mip_gap=max(0., cost-bound)/max(1e-9, abs(cost)),
                        proven_optimal=False, proven_infeasible=False, runtime=elapsed,
                        incumbent_retained=True, last_solver_status=info['status'])
    return kept
