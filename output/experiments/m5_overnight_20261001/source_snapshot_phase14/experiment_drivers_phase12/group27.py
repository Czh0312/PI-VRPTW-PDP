from pathlib import Path
import sys,json,hashlib,os
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_group27'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_group_schedule import conditional_group_schedule
for p,h in json.loads((HERE/'manifest_group27_input.json').read_text())['sha256'].items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==h
problem=load_instance(*load_routing_case(str(HERE/'medium25/0/inputs/case_relocated.json')))
groups=json.loads((HERE/'group27/groups.json').read_text());out=HERE/'group27'
(out/'parameters.json').write_text(json.dumps(dict(time_limit=90,seed=42,every_group_permutation=True,
 keep_cost_dominated_paths=True,full_vehicle_positions=True,fixed20ordered_control='cover26/outcome.json',
 same_frozen_input=True,same_condition_group_partition=True,not_primal_timing_comparison=True),indent=2)+'\n')
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='running_group27',active_process=os.getpid());(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
r=conditional_group_schedule(problem,groups,time_limit=90,seed=42,diagnostics_dir=out/'models')
(out/'outcome.json').write_text(json.dumps(r,indent=2)+'\n')
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='group27_complete',active_process=None);(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
print(json.dumps(r['certificate'],indent=2))
