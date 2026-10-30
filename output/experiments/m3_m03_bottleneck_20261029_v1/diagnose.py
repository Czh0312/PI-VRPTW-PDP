"""Read-only M03 diagnosis from frozen runs, input geometry and tiny auxiliary LPs."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys
import time

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))
PREVIOUS = ROOT/'output/experiments/m3_all_medium_exact_1800s_20261029_v1'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def native_log(path):
    content = path.read_text(encoding='utf-8-sig')
    root = re.search(r'Root relaxation: objective ([\deE+.\-]+), (\d+) iterations, ([\d.]+) seconds', content)
    points, root_rows, branch_bound = [], [], None
    for line in content.splitlines():
        fields = line.split()
        is_solution = bool(fields and fields[0] in ('H', '*'))
        fields = fields[1:] if is_solution else fields
        if len(fields) < 6 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        gap_index = next((i for i, token in enumerate(fields) if token.endswith('%')), None)
        if gap_index is None:
            continue
        try:
            incumbent, bound = float(fields[gap_index-2]), float(fields[gap_index-1])
            elapsed = float(fields[-1].removesuffix('s'))
        except ValueError:
            continue
        point = dict(seconds=elapsed, incumbent=incumbent, bound=bound,
                     explored=int(fields[0]), unexplored=int(fields[1]))
        points.append(point)
        if point['explored'] == 0:
            root_rows.append(point)
            if point['unexplored'] > 0 and branch_bound is None:
                branch_bound = bound
    warnings = [line for line in content.splitlines() if re.search(
        r'Numerical trouble|Warning:.*(?:numeric|coefficient|violation|large)|unscaled.*violation', line, re.I)]
    return dict(root_lp_value=float(root.group(1)) if root else None,
                root_lp_iterations=int(root.group(2)) if root else None,
                root_lp_seconds=float(root.group(3)) if root else None,
                root_after_cuts_bound=branch_bound if branch_bound is not None else
                    (root_rows[-1]['bound'] if root_rows else None),
                numeric_warning_lines=warnings, points=points)


def arc_lp_diagnostic(problem, params, site, arcs, nodes, tag):
    """The production auxiliary arc-cover LP, with its primal components exposed.

    This is a deliberately weak auxiliary relaxation, NOT the full MILP root
    LP. Its disconnected components diagnose the auxiliary bound only.
    """
    import gurobipy as gp
    from models.routing.model_3.exact.route_bounds import minimum_trip_count
    m = gp.Model('diagnostic_auxiliary_arc_cover')
    try:
        m.Params.OutputFlag = 1
        m.Params.LogToConsole = 0
        m.Params.LogFile = (OUT.relative_to(ROOT)/f'{tag}_aux_LP.log').as_posix()
        m.Params.Threads = 1
        m.Params.Seed = 42
        m.Params.TimeLimit = 5
        x = m.addVars(arcs, ub=1)
        incoming, outgoing = defaultdict(list), defaultdict(list)
        for a, b in arcs:
            incoming[b].append((a, b))
            outgoing[a].append((a, b))
        for i in range(len(nodes)):
            m.addConstr(gp.quicksum(x[e] for e in incoming[i]) == 1)
            m.addConstr(gp.quicksum(x[e] for e in outgoing[i]) == 1)
        minimum = minimum_trip_count(problem, site)
        m.addConstr(gp.quicksum(x[e] for e in outgoing[-1]) >= minimum)
        m.addConstr(gp.quicksum(x[e] for e in incoming[-2]) >= minimum)
        m.setObjective(gp.quicksum(x[a, b]*problem.distance(
            site if a == -1 else nodes[a]['site'], site if b == -2 else nodes[b]['site']) for a, b in arcs))
        m.optimize()
        assert m.Status == gp.GRB.OPTIMAL
        positive = [(a, b, float(x[a, b].X)) for a, b in arcs if x[a, b].X > 1e-7]
        adjacency = {i: set() for i in range(len(nodes))}
        anchored = set()
        for a, b, value in positive:
            if a == -1:
                anchored.add(b)
            elif b == -2:
                anchored.add(a)
            else:
                adjacency[a].add(b)
                adjacency[b].add(a)
        remaining, components = set(adjacency), []
        while remaining:
            stack, component = [next(iter(remaining))], set()
            while stack:
                at = stack.pop()
                if at in component:
                    continue
                component.add(at)
                stack.extend(adjacency[at]-component)
            remaining -= component
            components.append(component)
        floating = [component for component in components if not component & anchored]
        return dict(status=m.Status, value=m.ObjVal, work=m.Work, seconds=m.Runtime,
            minimum_trips=minimum, fractional_positive_edges=sum(abs(v-round(v)) > 1e-7 for a, b, v in positive),
            departures=sum(v for a, b, v in positive if a == -1),
            unanchored_components=len(floating), unanchored_tasks=sum(map(len, floating)),
            unanchored_task_lists=[[f'{nodes[i]["order"].id}:{nodes[i]["kind"]}' for i in sorted(component)]
                                   for component in floating])
    finally:
        m.dispose()


def analyze():
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.routing.model_3.exact.parameters import prepare_parameters
    from models.routing.model_3.exact.route_bounds import site_nodes
    from models.routing.model_3.exact.preprocessing.model_bounds import route_arcs, trip_slot_limits
    frozen = read(PREVIOUS/'plan.json')
    for relative, expected in frozen['hashes'].items():
        assert sha(ROOT/relative) == expected, relative
    rows, details = read(PREVIOUS/'rows.json'), []
    for row in rows:
        cfg, fleet = load_routing_case(ROOT/row['case_path'], use_pool_config=False)
        p = load_instance(cfg, fleet)
        params = prepare_parameters(None, None, p, True, None)
        result = read(PREVIOUS/'runs'/row['id']/'result.json')
        phase = next(phase for phase in row['native_phases'] if phase['phase'] == 'full_model')
        log = native_log(PREVIOUS/'runs'/row['id']/phase['logfile'])
        components = result['cost_components']
        constant = sum(components[key] for key in ('container_pool', 'cleaning', 'tracking', 'container_wear'))
        variable = row['objective']-constant
        sites = []
        for site in sorted({o.satellite for o in p.orders}):
            nodes = site_nodes(p, params, site)
            arcs = route_arcs(p, nodes, site, 'e2')
            arc_types = Counter((nodes[a]['kind'] if a >= 0 else 'source',
                                 nodes[b]['kind'] if b >= 0 else 'sink') for a, b in arcs)
            aux = arc_lp_diagnostic(p, params, site, arcs, nodes, f'{row["case"]}_{site}')
            recorded = result['info']['route_strengthening']['sites'][site]['assignment']
            if recorded.get('kind') == 'arc_cover_LP':
                assert abs(aux['value']-recorded['lower_bound']) <= 1e-6
            sites.append(dict(site=site, orders=sum(o.satellite == site for o in p.orders), tasks=len(nodes),
                legal_arcs=len(arcs), legal_internal_arcs=sum(a >= 0 and b >= 0 for a, b in arcs),
                arc_types={f'{a}->{b}': value for (a, b), value in sorted(arc_types.items())},
                available_vehicle_trip_slots=trip_slot_limits(p, len(nodes)), auxiliary_LP=aux,
                complete_enumeration=result['info']['route_strengthening']['sites'][site]['complete_library']))
        gaps = []
        waits = []
        for trip in result['e2_trips']:
            at, clock = trip['satellite_id'], trip['departure']
            for event in trip['events']:
                order = p.order_map[event['order_id']]
                idle = event['start']-clock-p.travel(at, order.customer)
                assert idle >= -1e-7
                waits.append(idle/60)
                at, clock = order.customer, event['start']+order.service_time
        for label, value in [('root_lp', log['root_lp_value']), ('root_after_cuts', log['root_after_cuts_bound']),
                             ('final_global_bound', row['bound'])]:
            if value is not None:
                gaps.append(dict(label=label, bound=value,
                    total_cost_relative_gap=(row['objective']-value)/row['objective'],
                    transport_cost_relative_gap=(row['objective']-value)/variable))
        native_start = row['initial_cost']
        item = dict(case=row['case'], historical_run_seconds=row['total_seconds'], status=row['status'],
            proven_optimal=row['optimal'], audited_feasible=row['feasible'], objective=row['objective'],
            bound=row['bound'], constant_cost=constant, transport_cost=variable, cost_components=components,
            comparisons_with_best_known_feasible=gaps,
            root_log=log, nodes=row['main_nodes'], main_work=row['main_work'],
            binary=row['binary'], variables=row['variables'], linear_rows=phase['linear_constraints'],
            general_constraints=phase['general_constraints'], initial_cost=native_start,
            initial_full_schedule_ready_seconds=next((phase['ends_after_seconds'] for phase in row['native_phases']
                if phase['phase'] == 'internal_schedule'), None),
            delivery_window_minutes=sorted({(o.delivery_window[1]-o.delivery_window[0])/60 for o in p.orders}),
            pickup_window_minutes=sorted({(o.pickup_window[1]-o.pickup_window[0])/60 for o in p.orders}),
            service_minutes=sorted({o.service_time/60 for o in p.orders}),
            min_use_minutes=sorted({o.min_use_time/60 for o in p.orders}),
            unique_customers=len({o.customer for o in p.orders}),
            unique_coordinates=len({(p.network.nodes[o.customer].x, p.network.nodes[o.customer].y) for o in p.orders}),
            sites=sites, used_e2_trips=len(result['e2_trips']),
            used_e2_vehicles=len({(t['satellite_id'], t['vehicle_id']) for t in result['e2_trips']}),
            max_reported_inter_event_wait_minutes=max(waits, default=0),
            longest_reported_trip_minutes=max((t['return_complete']-t['departure'])/60 for t in result['e2_trips']),
            model_time_tick_seconds=p.operating_policy.time_tick_seconds)
        details.append(item)
        print(json.dumps({key: item[key] for key in ('case', 'historical_run_seconds', 'objective', 'bound',
              'binary', 'used_e2_trips', 'used_e2_vehicles', 'max_reported_inter_event_wait_minutes')}, ensure_ascii=False), flush=True)
    for relative, expected in frozen['hashes'].items():
        assert sha(ROOT/relative) == expected, relative
    save(OUT/'analysis.json', details)
    save(OUT/'validation.json', dict(historical_runs_recomputed=False, new_full_MILP_runs=0,
        input_or_production_model_modified=False, historical_source_and_input_hashes_valid=True,
        auxiliary_LP_calls=sum(len(item['sites']) for item in details), auxiliary_LP_seed=42,
        auxiliary_LP_seconds_limit=5, auxiliary_LP_threads=1,
        distinction='Auxiliary arc-cover LP components are NOT measurements of the complete MILP root primal.',
        one_hour_run_evidence_found=False, latest_M03_continuous_run_budget_seconds=1800))


if __name__ == '__main__':
    analyze()
