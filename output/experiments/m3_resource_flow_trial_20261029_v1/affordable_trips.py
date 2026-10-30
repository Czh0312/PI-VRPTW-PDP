"""Candidate optimality-preserving trip bounds from a fresh audited witness."""
import math
import time


def restrict_affordable_trips(problem, model, context, bounds, witness, *, deadline, threads=1, seed=42):
    import gurobipy as gp
    from models.routing.model_3.exact.route_bounds import site_nodes, minimum_trip_count
    from models.routing.model_3.exact.preprocessing.model_bounds import route_arcs
    from models.routing.model_3.exact.precedence_cuts import conflict_pairs
    from models.common.cycle.audit import audit_solution
    begun = time.perf_counter()
    info = dict(applied=False, sites={}, fixed_zero_slots=0, source='fresh_audited_internal_start')
    if not getattr(problem, 'cost_objective', False) or problem.free_assignment:
        return dict(info, reason='outside_cost_fixed_assignment_scope')
    audit = audit_solution(problem, witness)
    if not audit.get('valid') or not audit.get('periodic_replay', {}).get('valid'):
        return dict(info, reason='witness_audit_failed')
    upper = audit['objective']
    rates = problem.cost_profile.rates
    if any(value < 0 for value in rates.values() if isinstance(value, (int, float))):
        return dict(info, reason='negative_rate')
    e1 = rates['e1_distance_cost_per_km']*rates['distance_scale_to_km']
    e2 = rates['e2_distance_cost_per_km']*rates['distance_scale_to_km']
    trip_fee = rates.get('e2_trip_cost', 0.)
    if e2 <= 0 and trip_fee <= 0:
        return dict(info, reason='no_positive_trip_or_distance_price')
    constant = rates['container_pool_cost_per_period']*problem.operating_policy.container_pool_size
    constant += sum(problem.cost_profile.constant_cost(problem.total).values())
    sites = sorted(bounds.get('sites', {}))
    for index, site in enumerate(sites):
        if time.perf_counter() >= deadline:
            break
        site_deadline = time.perf_counter()+(deadline-time.perf_counter())/(len(sites)-index)
        nodes = site_nodes(problem, context['parameters'], site)
        arcs = route_arcs(problem, nodes, site, 'e2')
        other = sum(value['distance_lower_bound'] for key, value in bounds['sites'].items() if key != site)
        available = upper-constant-e1*bounds.get('e1_distance_lower_bound', 0.)-e2*other
        available -= trip_fee*sum(minimum_trip_count(problem, key) for key in sites if key != site)
        relaxation = gp.Model('m3_maximum_affordable_trips')
        try:
            relaxation.Params.OutputFlag = 0
            relaxation.Params.Threads = threads
            relaxation.Params.Seed = seed
            x = relaxation.addVars(arcs, ub=1.)
            incoming, outgoing = {i: [] for i in range(len(nodes))}, {i: [] for i in range(len(nodes))}
            for edge in arcs:
                if edge[1] >= 0:
                    incoming[edge[1]].append(edge)
                if edge[0] >= 0:
                    outgoing[edge[0]].append(edge)
            for i in range(len(nodes)):
                relaxation.addConstr(gp.quicksum(x[e] for e in incoming[i]) == 1)
                relaxation.addConstr(gp.quicksum(x[e] for e in outgoing[i]) == 1)
            departures = gp.quicksum(x[a, b] for a, b in arcs if a == -1)
            relaxation.addConstr(departures >= minimum_trip_count(problem, site))
            for first, second in conflict_pairs(nodes, arcs):
                relaxation.addConstr(x[first]+x[second] <= 1)
            for cut in bounds['sites'][site]['assignment'].get('capacity_cuts', []):
                chosen = set(cut['tasks'])
                relaxation.addConstr(gp.quicksum(x[a, b] for a, b in arcs if a in chosen and b not in chosen)
                                     >= cut['required'])
            transport = gp.quicksum(x[a, b]*problem.distance(
                site if a == -1 else nodes[a]['site'], site if b == -2 else nodes[b]['site']) for a, b in arcs)
            relaxation.addConstr(e2*transport+trip_fee*departures <= available+1e-5)
            relaxation.setObjective(departures, gp.GRB.MAXIMIZE)
            relaxation.Params.TimeLimit = max(0., site_deadline-time.perf_counter())
            relaxation.optimize()
            evidence = dict(native_status=relaxation.Status, seconds=relaxation.Runtime, work=relaxation.Work)
            info['sites'][site] = evidence
            if relaxation.Status != gp.GRB.OPTIMAL:
                evidence['reason'] = 'no_optimal_upper_bound'
                continue
            raw_upper = float(relaxation.ObjVal)
            # Round UP conservatively rather than risk a tight floor near an
            # integer. A noninteger LP optimum upper-bounds the integer count.
            limit = min(len(nodes), math.ceil(raw_upper+1e-6*max(1., abs(raw_upper))))
            known_trips = sum(trip['satellite_id'] == site for trip in witness['e2_trips'])
            if limit < known_trips or limit < minimum_trip_count(problem, site):
                evidence['reason'] = 'upper_bound_inconsistent_with_audited_witness'
                continue
            routes = [r for r in context['e2'] if r['satellite'] == site]
            relaxation_count = gp.quicksum(r['used'] for r in routes)
            model.addConstr(relaxation_count <= limit, name=f'affordable_trip_count_{site}')
            fixed = 0
            for route in routes:
                vehicle_limit = limit//(route['vehicle']+1) if context.get('trip_count_symmetry') else limit
                if route['slot'] >= vehicle_limit and route['used'].UB > 0:
                    route['used'].UB = 0
                    fixed += 1
            evidence.update(relaxed_maximum=raw_upper, conservative_upper=limit,
                            audited_witness_trips=known_trips, fixed_zero_slots=fixed)
            info['fixed_zero_slots'] += fixed
            info['applied'] = True
        finally:
            relaxation.dispose()
    model.update()
    info.update(objective_upper=upper, total_seconds=time.perf_counter()-begun,
                feasible_domain='original optimum retained under audited feasible objective upper bound')
    return info
