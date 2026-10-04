from pathlib import Path
import sys,json,time,os,hashlib
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_phase13'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_physical_site import physical_site_certificate
assert json.loads((HERE/'master39/independent_validation.json').read_text())['promote_supported']
p=load_instance(*load_routing_case(str(HERE/'medium25/0/inputs/case_relocated.json')))
g=json.loads((HERE/'site40/groups.json').read_text());out=HERE/'site40'
(out/'parameters.json').write_text(json.dumps(dict(satellite='S1',time_limit=60,seed=42,trip_groups=g['S1'],partial_groups=False,original_problem=True),indent=2)+'\n')
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='running_site40',active_process=os.getpid());(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
r=physical_site_certificate(p,'S1',time_limit=60,seed=42,trip_groups=g['S1'],compress_routes=True,diagnostics_path=out/'physical.mps.gz')
(out/'certificate_pending_validation.json').write_text(json.dumps(r,indent=2)+'\n')
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='site40_pending_validation',active_process=None);(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n');print(json.dumps({k:v for k,v in r.items() if k!='projection_proof'},indent=2))
