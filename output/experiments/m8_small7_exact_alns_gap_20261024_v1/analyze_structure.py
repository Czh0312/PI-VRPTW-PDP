"""Read frozen inputs/logs; no model construction or optimization."""
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE/'source'))
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.routing.model_8.exact.parameters import necessary_time_bounds


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def possibilities(remaining, k):
    # Forced orders occupy batch 1; every other batch must be nonempty.
    return sum((-1)**j * math.comb(k-1, j) * (k-j)**remaining for j in range(k))


rows = []
for number in range(1, 10):
    case = f'S{number:02d}'
    p = load_instance(*load_routing_case(HERE/'cases'/f'{case}.json', use_pool_config=False))
    bound = necessary_time_bounds(p)
    forced = [r['order'] for r in bound['forced_first_batch']]
    distribution = dict(Counter(o.satellite for o in p.orders))
    tpm = 60/p.operating_policy.time_tick_seconds
    orders = [dict(id=o.id, site=o.satellite,
                   delivery=[x/tpm for x in o.delivery_window], pickup=[x/tpm for x in o.pickup_window],
                   forced_first=o.id in forced) for o in p.orders]
    k_upper = min(len(p.orders), len(p.orders)-len(forced)+1)
    assignments = {k: possibilities(len(p.orders)-len(forced), k)
                   for k in p.batch_count_candidates if k <= k_upper}
    arcs = {}
    for site in p.satellites:
        tasks = [(o, kind, o.delivery_window if kind == 'd' else o.pickup_window)
                 for o in p.orders if o.satellite == site for kind in ('d', 'p')]
        allowed = 0
        for left, lk, lw in tasks:
            for right, rk, rw in tasks:
                if left.id == right.id and (lk == rk or (lk == 'p' and rk == 'd')):
                    continue
                if lw[0]+left.service_time+p.travel(left.customer, right.customer) <= rw[1]:
                    allowed += 1
        arcs[site] = dict(tasks=len(tasks), internal_allowed=allowed,
                          route_slots=p.fleet.e2_per_sat_count*min(p.operating_policy.e2_max_trips_per_vehicle,len(tasks)))
    job = HERE/'runs/exact'/case
    if not job.exists() and (HERE/'interrupted/exact'/f'{case}_attempt_001').exists():
        job = HERE/'interrupted/exact'/f'{case}_attempt_001'
    log = job/'physical_time.log'
    text = log.read_text(encoding='utf-8') if log.exists() else ''
    match = re.search(r'Optimize a model with (\d+) rows, (\d+) columns and (\d+) nonzeros', text)
    presolved = re.search(r'Presolved: (\d+) rows, (\d+) columns, (\d+) nonzeros', text)
    root = re.search(r'Root relaxation: objective ([\d.e+\-]+)', text)
    summary = read(job/'summary.json') if (job/'summary.json').exists() else {}
    row = dict(case=case, distribution=distribution, forced_first=forced,
               pool_lb=bound['pool_lower_bound'], necessary_K_upper=k_upper,
               assignment_patterns_by_K=assignments, assignment_patterns_total=sum(assignments.values()),
               E2_arcs=arcs, E2_internal_arc_copies=sum(v['internal_allowed']*v['route_slots'] for v in arcs.values()),
               orders=orders, fleet=vars(p.fleet),
               input_rows=int(match[1]) if match else None, input_columns=int(match[2]) if match else None,
               presolved_rows=int(presolved[1]) if presolved else None,
               presolved_columns=int(presolved[2]) if presolved else None,
               root_relaxation=float(root[1]) if root else None,
               result={k:summary.get(k) for k in ('status','objective','best_bound','selected_K','pool',
                         'node_count','build_time','mip_runtime','first_solver_incumbent_seconds')})
    if case == 'S03':
        row['interrupted_live'] = read(job/'live.json')
        row['interrupted_log_tail'] = text.splitlines()[-1] if text else None
    rows.append(row)
output = dict(scope='input structure and existing logs only; no optimization',
              caveat='Assignment counts are combinatorial upper bounds after forced-first/nonempty deductions; other timing, routes and capacities further reduce them.',
              rows=rows)
(HERE/'structural_analysis.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
for row in rows[:3]:
    print(json.dumps(row,ensure_ascii=False))
