"""Exploratory joint inventory on previously independently certified universes."""
import gzip
import json
from pathlib import Path
import sys
import time
here = Path(__file__).resolve().parent
root = here.parents[2]
sys.path.insert(0, str(root))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_joint_inventory import solve_joint_inventory
from models.routing.solvers.exact.multibatch_route_pricing import route_context
from models.routing.common.cycle.precheck import travel_lower_bounds

previous = here.parent/'m5_integer_cover_20261001/final_60s'
reports = []
for case in range(3):
    payload = json.loads((previous/f'{case:02d}_42_integer.json').read_text())
    job, reference = payload['job'], payload['certificate']
    problem = load_instance(*load_routing_case(job['case']))
    initial = json.loads(Path(job['initial_result']).read_text())
    travel, domains = travel_lower_bounds(problem), {}
    for i, site in enumerate(reference['sites']):
        with gzip.open(previous/f'{case:02d}_42_integer_pricing_models/site_{i}_best_pricing.json.gz', 'rt') as stream:
            universe = json.load(stream)
        domains[site['satellite']] = route_context(problem, site['satellite'], travel), universe
    result = solve_joint_inventory(problem, domains, deadline=time.perf_counter()+30, initial_result=initial,
        travel_bounds=travel, distance_bounds=reference['distance_certificate'], e1_bound=reference['e1_bound'],
        site_bounds={s['satellite']: s['bound'] for s in reference['sites']}, fallback_bound=reference['bound'],
        verbose=True, diagnostics_dir=here/f'probe_{case}_models')
    reports.append(dict(customers=job['customers'], upper=job['frozen_upper_bound'],
        gap=(job['frozen_upper_bound']-result['bound'])/job['frozen_upper_bound'], joint=result))
    (here/'probe.json').write_text(json.dumps(reports, indent=2)+'\n')
    print(json.dumps(reports[-1]), flush=True)
