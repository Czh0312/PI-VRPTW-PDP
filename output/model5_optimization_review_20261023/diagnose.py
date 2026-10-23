"""Read-only M5 diagnostics; no solver runs or algorithm mutations."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from models.common.cycle import load_instance
from models.common.cycle.precheck import check_necessary_conditions
from models.routing.model_5.alns.evaluator import _e1_returns, route_distance
from tools.configuration.loader import load_routing_case


def calendar_counts(case):
    path = ROOT / 'data/formal_benchmark27_spacetime_v2/model_5/large' / case / 'case.json'
    problem = load_instance(*load_routing_case(path))
    policy = problem.operating_policy
    counts = Counter()
    pairs = []
    for loading in policy.batches:
        for recovery in policy.batches:
            if recovery.cutoff + policy.cleaning_duration > loading.start:
                kind = 'necessarily_zero'
            elif recovery.end + policy.cleaning_duration <= loading.start:
                kind = 'necessarily_full_quantity'
            else:
                kind = 'conditional'
            counts[kind] += problem.fleet.e1_count
            pairs.append(dict(loading=loading.id, recovery=recovery.id,
                              classification=kind, routes=problem.fleet.e1_count))
    check = check_necessary_conditions(problem)
    total = sum(counts.values())
    return dict(case=case, customers=len({o.customer for o in problem.orders}),
                precheck_passed=check['passed'], pool_min=problem.pool_min,
                necessary_pool_lower_bound=check['pool_lower_bound'],
                pool_max=problem.pool_max, route_loading_pairs=total,
                counts=dict(counts), auxiliary_variables_before=3*total,
                auxiliary_variables_after_calendar_classification=3*counts['conditional'],
                counts_scope='inventory release block only; before Gurobi presolve',
                pairs=pairs)


def decoder_example():
    # Local decoder example only: two sites on a line, fixed delivery order B,A.
    # This is not an end-to-end benchmark or a proof of full-objective improvement.
    positions = {'D': 0, 'A': 10, 'B': 1}
    batch = NS(id='b', start=0, cutoff=100, end=200)
    p = NS(batches=[batch], cycle_start=0, cycle_end=300, e1_load_time=0,
           depot_return_unload_time=0, e1_turnaround_time=0, cleaning_duration=5)
    f = NS(e1_count=2, e1_weight_cap=100, e1_volume_cap=100,
           container_weight=1, container_volume=1)
    problem = NS(operating_policy=p, fleet=f, depot='D',
                 weights={('A','b'):1, ('B','b'):1},
                 demands={('A','b'):1, ('B','b'):1},
                 distance=lambda a,b: abs(positions[a]-positions[b]),
                 travel=lambda a,b: abs(positions[a]-positions[b]),
                 service=lambda s: 0)
    state = dict(deliveries={'b': [['B','A'], []]},
                 pickup_order={'b': ['B','A']}, pickup_vehicle={'b': {}})
    result = {}
    for label, hints in [('no_hints', {}), ('both_pickups_on_delivery_truck', {'A':0,'B':0})]:
        state['pickup_vehicle']['b'] = hints
        trips, issues, releases = _e1_returns(problem, state, {'b': {'A':1,'B':1}})
        result[label] = dict(total_distance=sum(route_distance(problem,t,'e1') for t in trips),
                             violations=issues, releases=releases, trips=trips)
    assert result['no_hints']['total_distance'] == 22
    assert result['both_pickups_on_delivery_truck']['total_distance'] == 20
    assert not any(v['violations'] for v in result.values())
    return result


if __name__ == '__main__':
    paths = ['models/routing/model_5/alns/evaluator.py',
             'models/routing/model_5/exact/constraint_blocks/inventory.py',
             'models/routing/model_5/exact/formulation.py',
             'models/routing/model_5/exact/milp_solver.py']
    result = dict(kind='static_diagnostics_not_solver_experiment',
                  calendar=[calendar_counts(c) for c in ('L06','L07')],
                  decoder_example=decoder_example(),
                  source_sha256={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths})
    Path(__file__).with_name('diagnostics.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(dict(calendar=[{k:v for k,v in c.items() if k!='pairs'} for c in result['calendar']],
                         decoder_distances={k:v['total_distance'] for k,v in result['decoder_example'].items()}),
                     ensure_ascii=False, indent=2))
