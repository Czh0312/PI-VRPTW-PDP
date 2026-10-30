"""Diagnostic only: can a fresh feasible cost imply a safe trip-count upper bound?"""
import json
import math
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))
import gurobipy as gp
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.routing.model_3.exact.parameters import prepare_parameters
from models.routing.model_3.exact.route_bounds import site_nodes, minimum_trip_count
from models.routing.model_3.exact.preprocessing.model_bounds import route_arcs
from models.routing.model_3.exact.precedence_cuts import conflict_pairs


if __name__ == '__main__':
    answers = []
    for case in ('M03', 'M05', 'M07'):
        directory = ROOT/f'output/experiments/m3_network_strengthening_20261029_v1/runs/{case}_after_s42_300s'
        result = json.loads((directory/'result.json').read_text(encoding='utf-8'))
        cfg, fleet = load_routing_case(ROOT/f'data/formal_benchmark27_uniform_7_20_50_v1/model_3/medium/{case}/case.json', use_pool_config=False)
        p = load_instance(cfg, fleet)
        params = prepare_parameters(None, None, p, True, None)
        rates = p.cost_profile.rates
        constant = rates['container_pool_cost_per_period']*p.operating_policy.container_pool_size+sum(p.cost_profile.constant_cost(p.total).values())
        bound = result['info']['route_strengthening']
        for site in sorted({o.satellite for o in p.orders}):
            nodes = site_nodes(p, params, site)
            arcs = route_arcs(p, nodes, site, 'e2')
            m = gp.Model('diagnostic_maximum_trips_under_feasible_cost')
            try:
                m.Params.OutputFlag = 0
                m.Params.Seed = 42
                m.Params.Threads = 1
                m.Params.TimeLimit = 2
                x = m.addVars(arcs, ub=1)
                for i in range(len(nodes)):
                    m.addConstr(gp.quicksum(x[a, b] for a, b in arcs if b == i) == 1)
                    m.addConstr(gp.quicksum(x[a, b] for a, b in arcs if a == i) == 1)
                departures = gp.quicksum(x[a, b] for a, b in arcs if a == -1)
                m.addConstr(departures >= minimum_trip_count(p, site))
                for first, second in conflict_pairs(nodes, arcs):
                    m.addConstr(x[first]+x[second] <= 1)
                for cut in bound['sites'][site]['assignment'].get('capacity_cuts', []):
                    chosen = set(cut['tasks'])
                    m.addConstr(gp.quicksum(x[a, b] for a, b in arcs if a in chosen and b not in chosen) >= cut['required'])
                other = sum(v['distance_lower_bound'] for s, v in bound['sites'].items() if s != site)
                e1 = bound['e1_distance_lower_bound']*rates['e1_distance_cost_per_km']*rates['distance_scale_to_km']
                e2_rate = rates['e2_distance_cost_per_km']*rates['distance_scale_to_km']
                upper = result['info']['internal_start']['objective']
                distance_budget = (upper-constant-e1)/e2_rate-other
                m.addConstr(gp.quicksum(x[a, b]*p.distance(site if a == -1 else nodes[a]['site'], site if b == -2 else nodes[b]['site']) for a, b in arcs) <= distance_budget+1e-5)
                m.setObjective(departures, gp.GRB.MAXIMIZE)
                m.optimize()
                answers.append(dict(case=case, site=site, status=m.Status, cost_upper=upper,
                    distance_budget=distance_budget, relaxed_maximum_trips=m.ObjVal if m.Status == 2 else None,
                    conservative_integer_upper=math.ceil(m.ObjVal+1e-6) if m.Status == 2 else None,
                    seconds=m.Runtime))
            finally:
                m.dispose()
    (OUT/'trip_limit_probe.json').write_text(json.dumps(answers, indent=2), encoding='utf-8')
    print(json.dumps(answers), flush=True)
