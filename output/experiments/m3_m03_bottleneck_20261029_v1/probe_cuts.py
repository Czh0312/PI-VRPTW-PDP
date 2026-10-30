"""Diagnostic component capacity cuts in the AUXILIARY LP, not a new full solver."""
from collections import defaultdict
import time

from diagnose import OUT, ROOT, PREVIOUS, read, save


def probe(case):
    import gurobipy as gp
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.routing.model_3.exact.parameters import prepare_parameters
    from models.routing.model_3.exact.route_bounds import site_nodes, minimum_trip_count
    from models.routing.model_3.exact.preprocessing.model_bounds import route_arcs
    import math
    cfg, fleet = load_routing_case(ROOT/f'data/formal_benchmark27_uniform_7_20_50_v1/model_3/medium/{case}/case.json', use_pool_config=False)
    p = load_instance(cfg, fleet)
    parameters = prepare_parameters(None, None, p, True, None)
    site, = {o.satellite for o in p.orders}
    nodes = site_nodes(p, parameters, site)
    arcs = route_arcs(p, nodes, site, 'e2')
    m = gp.Model('diagnostic_connected_capacity_arc_LP')
    deadline = time.perf_counter()+15
    try:
        m.Params.OutputFlag = 1
        m.Params.LogToConsole = 0
        m.Params.LogFile = (OUT.relative_to(ROOT)/f'{case}_connected_capacity_LP.log').as_posix()
        m.Params.Threads = 1
        m.Params.Seed = 42
        x = m.addVars(arcs, ub=1)
        incoming, outgoing = defaultdict(list), defaultdict(list)
        for a, b in arcs:
            incoming[b].append((a, b))
            outgoing[a].append((a, b))
        for i in range(len(nodes)):
            m.addConstr(gp.quicksum(x[e] for e in incoming[i]) == 1)
            m.addConstr(gp.quicksum(x[e] for e in outgoing[i]) == 1)
        m.addConstr(gp.quicksum(x[e] for e in outgoing[-1]) >= minimum_trip_count(p, site))
        m.addConstr(gp.quicksum(x[e] for e in incoming[-2]) >= minimum_trip_count(p, site))
        m.setObjective(gp.quicksum(x[a, b]*p.distance(
            site if a == -1 else nodes[a]['site'], site if b == -2 else nodes[b]['site']) for a, b in arcs))
        history, cuts, known = [], 0, set()
        reason = 'budget_exhausted'
        while time.perf_counter() < deadline:
            m.Params.TimeLimit = max(0, deadline-time.perf_counter())
            m.optimize()
            if m.Status != gp.GRB.OPTIMAL:
                reason = 'incomplete_LP_no_new_bound_used'
                break
            values = {edge: x[edge].X for edge in arcs}
            adjacency = {i: set() for i in range(len(nodes))}
            for (a, b), value in values.items():
                if a >= 0 and b >= 0 and value > 1e-7:
                    adjacency[a].add(b)
                    adjacency[b].add(a)
            remaining, components = set(adjacency), []
            while remaining:
                stack, component = [next(iter(remaining))], set()
                while stack:
                    at = stack.pop()
                    if at not in component:
                        component.add(at)
                        stack.extend(adjacency[at]-component)
                remaining -= component
                components.append(component)
            history.append(dict(round=len(history), optimal_LP_bound=m.ObjVal, cuts=cuts,
                                components=len(components), seconds=15-(deadline-time.perf_counter())))
            new = 0
            for component in components:
                required = 1
                for kind in p.task_kinds:
                    for key, capacity in (('w', p.fleet.e2_weight_cap), ('v', p.fleet.e2_volume_cap)):
                        total = sum(nodes[i][key] for i in component if nodes[i]['kind'] == kind)
                        if capacity > 0:
                            required = max(required, math.ceil(total/capacity-1e-9))
                boundary = [(a, b) for a, b in arcs if a in component and b not in component]
                if sum(values[edge] for edge in boundary) < required-1e-7:
                    signature = tuple(sorted(component))
                    if signature not in known:
                        # Every physical trip starts at the base and carries
                        # at most C of all its deliveries/all its pickups.
                        # At least required distinct trips serve this subset;
                        # each such trip must leave it at least once.
                        m.addConstr(gp.quicksum(x[e] for e in boundary) >= required)
                        known.add(signature)
                        cuts += 1
                        new += 1
            if not new:
                reason = 'no_violated_support_component_cut_found'
                break
        previous = next(row for row in read(PREVIOUS/'rows.json') if row['case'] == case)
        result = read(PREVIOUS/'runs'/previous['id']/'result.json')
        physical_distance = result['cost_components']['e2_distance']/.05
        assert all(record['optimal_LP_bound'] <= physical_distance+1e-6 for record in history)
        return dict(case=case, seed=42, seconds_budget=15, threads=1, cuts=cuts,
            initial_distance_LP_bound=history[0]['optimal_LP_bound'],
            final_distance_LP_bound=history[-1]['optimal_LP_bound'],
            known_feasible_e2_distance=physical_distance, reason=reason, history=history,
            complete_subset_cut_separation=False, complete_original_model_solved=False)
    finally:
        m.dispose()


if __name__ == '__main__':
    results = [probe(case) for case in ('M03', 'M05', 'M07')]
    save(OUT/'capacity_cut_probe.json', results)
    for result in results:
        print({key: value for key, value in result.items() if key != 'history'}, flush=True)
