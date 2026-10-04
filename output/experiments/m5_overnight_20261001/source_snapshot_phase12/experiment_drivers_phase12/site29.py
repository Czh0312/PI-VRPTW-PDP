from pathlib import Path
import sys,json,time,os
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_group28'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_physical_site import physical_site_certificate
problem=load_instance(*load_routing_case(str(HERE/'medium25/0/inputs/case_relocated.json')))
groups=json.loads((HERE/'group27/groups.json').read_text());out=HERE/'site29'
(out/'parameters.json').write_text(json.dumps(dict(satellite='S1',time_limit=60,seed=42,trip_groups=groups['S1'],
 original_business=True,local_pool_relaxed_and_E1_removed=True,conditional_E2_bound_only=True),indent=2)+'\n')
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='running_site29',active_process=os.getpid());(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
r=physical_site_certificate(problem,'S1',time_limit=60,seed=42,trip_groups=groups['S1'],compress_routes=True,
 diagnostics_path=out/'physical.mps.gz')
(out/'certificate_pending_validation.json').write_text(json.dumps(r,indent=2)+'\n')
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='site29_pending_validation',active_process=None);(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n');print(json.dumps(r,indent=2))
