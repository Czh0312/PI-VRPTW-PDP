from pathlib import Path
import sys,json,time,hashlib,os
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_cover24'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_cover_pool import reoptimize_cover_pool
manifest=json.loads((HERE/'manifest_cover24_input.json').read_text())['sha256']
for path,digest in manifest.items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
initial=json.loads((HERE/'pool22/initial_result.json').read_text())
covers={'S1':json.loads((HERE/'pool22/physical_columns.json').read_text())}
proposals=json.loads((HERE/'pool_after_vehicle4/additional_columns.json').read_text())
parameters=dict(time_limit=90,seed=42,max_columns=64,transfers=True,exchanges=True,recombinations=True,
 additional_columns_per_slot=32,extra_slots_per_vehicle=1)
out=HERE/'cover24'
(out/'parameters.json').write_text(json.dumps(dict(**parameters,same_input=True,same_initial=True,
 same_total_budget=True,recorded_32_control='pool_retention23/summary.json',recorded_64_control='pool22/control/result.json',
 comparison_sequential=True),indent=2)+'\n')
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='running_cover24',active_process=os.getpid());(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
begun=time.perf_counter()
result=reoptimize_cover_pool(problem,initial,covers,**parameters,additional_columns=proposals,diagnostics_dir=out/'models')
audit=audit_solution(problem,result);assert audit['valid']
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n');(out/'audit.json').write_text(json.dumps(audit,indent=2)+'\n')
summary=dict(objective=audit['objective'],runtime=time.perf_counter()-begun,info=result['info'],
 full_proven_optimal=False,full_proven_infeasible=False,scope='restricted_cover_preserving_pool_full_upper_only')
(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='cover24_complete',active_process=None);(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
print(json.dumps(summary,indent=2))
