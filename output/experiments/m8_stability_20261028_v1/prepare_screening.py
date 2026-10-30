"""Compute the conditional exact-cover cost certificates once per retained K."""
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0,str(ROOT))
from prepare import module,read,save
from cost_screening import screen_route_library
from models.common.cycle import load_instance
from tools.configuration.loader import load_routing_case

phase = module(ROOT/'output/experiments/m8_s04_reduction_validation_20261026_v1/phase_bound.py','verified_phase_patterns')
for case in ['S03','S08','S05']:
    begun = time.perf_counter()
    folder = HERE/'inputs'/case
    context = read(folder/'context.json')
    library = read(folder/'trip_library.json')
    p = load_instance(*load_routing_case(Path(context['case_path']),use_pool_config=False))
    patterns = phase.phase_patterns(p)
    for k in context['reduced_K']:
        child = p.for_count(k)
        assert child.operating_policy.time_tick_seconds == p.operating_policy.time_tick_seconds
        kept,certificate = screen_route_library(child,library,context['audited_upper_bound'],context['reduced_N'][0],patterns)
        certificate.update(K=k,case=case,wall_seconds=time.perf_counter()-begun)
        save(folder/f'screening_K{k}.json',certificate)
        save(folder/f'filtered_library_K{k}.json',kept)
        print(json.dumps({key:certificate[key] for key in ('case','K','original_columns','retained_columns','separable_cost_lower_bound','wall_seconds')}),flush=True)
