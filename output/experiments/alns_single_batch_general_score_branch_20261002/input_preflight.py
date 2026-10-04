from pathlib import Path
import sys,json
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(OUT/'before_source'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.precheck import check_necessary_conditions,travel_lower_bounds
checks=[]
for job in json.loads((OUT/'quality_manifest.json').read_text())['jobs']:
    config,fleet=load_routing_case(job['case']);p=load_instance({**config,'model':job['model']},fleet)
    pre=check_necessary_conditions(p,travel_lower_bounds(p));size=len({o.customer for o in p.orders})
    paths={k:v for k,v in config.items() if k in ('data','orders','routing_config','pool_config','algorithm_config','container_pool_config','dataset_metadata') and isinstance(v,str)}
    closed=all(Path(v).is_file() and str(Path(v).resolve()).startswith(str(OUT)) for v in paths.values())
    checks.append(dict(job=job,customers=size,orders=len(p.orders),precheck_passed=pre['passed'],input_paths=paths,all_paths_closed=closed,passed=pre['passed'] and size==job['size'] and closed))
(OUT/'input_preflight.json').write_text(json.dumps(checks,indent=2))
print(json.dumps([(r['job']['family'],r['passed']) for r in checks]))
assert all(r['passed'] for r in checks)
