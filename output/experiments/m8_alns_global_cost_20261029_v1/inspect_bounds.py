"""Input-only bound diagnostics; no optimizer or optimal-reference input."""
import json
import sys
from bench import ROOT, HERE, save
sys.path.insert(0, str(ROOT))
from models.common.cycle import load_instance
from tools.configuration.loader import load_routing_case
from models.routing.model_8.exact.preprocessing import temporal_assignment_bounds
from models.routing.model_8.exact.parameters import necessary_time_bounds

rows = []
for case in [f'S{i:02}' for i in range(1, 10)]:
    p = load_instance(*load_routing_case(ROOT/f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json', use_pool_config=False))
    variants = []
    for k in p.batch_count_candidates:
        child = p.for_count(k)
        bounds = temporal_assignment_bounds(child)
        variants.append(dict(K=k, certificate=bounds['certificate'], domains=bounds['domains'], removed=bounds['removed']))
    forced = [item['order'] for item in necessary_time_bounds(p)['forced_first_batch']]
    row = dict(case=case, forced_first=forced, certified_infeasible_K=[v['K'] for v in variants if v['certificate']], variants=variants)
    rows.append(row)
    print(json.dumps({key: value for key, value in row.items() if key != 'variants'}, ensure_ascii=False), flush=True)
save(HERE/'input_bounds_diagnosis.json', dict(purpose='Read-only pure-bound diagnostic; no exact optimization invoked.', rows=rows))
