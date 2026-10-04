from pathlib import Path
import sys,json,time,hashlib,os
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_cover26'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_cover_schedule import conditional_cover_schedule
manifest=json.loads((HERE/'manifest_cover26_input.json').read_text())['sha256']
for p,h in manifest.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==h
case=HERE/'medium25/0';problem=load_instance(*load_routing_case(str(case/'inputs/case_relocated.json')))
covers=json.loads((case/'covers.json').read_text());out=HERE/'cover26'
p=dict(time_limit=90,seed=42,all_physical_positions=True,every_active_site_fixed=True,original_business=True,global_infeasibility_or_bound_claim=False)
(out/'parameters.json').write_text(json.dumps(p,indent=2)+'\n')
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='running_cover26',active_process=os.getpid());(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
r=conditional_cover_schedule(problem,covers,time_limit=90,seed=42,diagnostics_dir=out/'models')
(out/'outcome.json').write_text(json.dumps(r,indent=2)+'\n')
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='cover26_complete',active_process=None);(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
print(json.dumps(r['certificate'],indent=2))
