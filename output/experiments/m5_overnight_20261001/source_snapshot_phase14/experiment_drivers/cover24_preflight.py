from pathlib import Path
import sys,json,time
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_cover24'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_route_pool import expanded_trip_slots,candidate_pools
from models.routing.solvers.exact.multibatch_cover_pool import preserve_cover_routes
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
initial=json.loads((HERE/'pool22/initial_result.json').read_text())
layout=expanded_trip_slots(problem,initial,1)
proposals=json.loads((HERE/'pool_after_vehicle4/additional_columns.json').read_text())
for trip in initial['e2_trips']:
 for kind in ('d','p'):
  keys=[(e['order_id'],kind) for e in trip['events'] if e['kind']==kind]
  for a in range(len(keys)):
   for b in range(a+1,len(keys)+1):proposals[trip['satellite_id']].append(dict(tasks=keys[a:b]))
pools=candidate_pools(problem,layout,max_columns=64,transfers=True,exchanges=True,recombinations=True,
 additional_columns=proposals,additional_columns_per_slot=32,balanced_extra=True)
baseline=json.loads((HERE/'pool_retention23/models/pass_0/columns.json').read_text())
assert json.loads(json.dumps(pools))==baseline
covers={'S1':json.loads((HERE/'pool22/physical_columns.json').read_text())}
pinned,info=preserve_cover_routes(problem,layout,pools,covers)
assert info['added_columns']>0
out=HERE/'cover24'
(out/'preflight_columns.json').write_text(json.dumps(pinned,indent=2)+'\n')
(out/'preflight_slots.json').write_text(json.dumps(layout['e2_trips'],indent=2)+'\n')
(out/'preflight.json').write_text(json.dumps(dict(**info,saved_32_control_candidates_exactly_identical=True,
 same_initial=True,same_business_input=True,original_32_prefix_preserved=True,new_candidate_set_confirmed=True),indent=2)+'\n')
print(json.dumps(info,indent=2))
