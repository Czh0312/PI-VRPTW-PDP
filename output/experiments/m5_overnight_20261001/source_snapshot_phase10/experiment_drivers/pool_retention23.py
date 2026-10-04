from pathlib import Path
import sys,json,time,os,hashlib
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_subset19'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_route_pool import reoptimize_route_pool
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
initial=json.loads((HERE/'pool22/initial_result.json').read_text())
proposals=json.loads((HERE/'pool_after_vehicle4/additional_columns.json').read_text())
parameters=json.loads((HERE/'pool22/parameters.json').read_text())
parameters={k:v for k,v in parameters.items() if k in ('time_limit','seed','max_columns','transfers','exchanges','recombinations','extra_slots_per_vehicle','max_passes','additional_columns_per_slot')}
parameters['additional_columns_per_slot']=32
out=HERE/'pool_retention23';(out/'parameters.json').write_text(json.dumps(parameters,indent=2)+'\n')
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='running_pool_retention23',active_process=os.getpid());(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
begun=time.perf_counter()
result=reoptimize_route_pool(problem,initial,**parameters,additional_columns=proposals,diagnostics_dir=out/'models')
audit=audit_solution(problem,result);assert audit['valid']
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n');(out/'audit.json').write_text(json.dumps(audit,indent=2)+'\n')
summary=dict(objective=audit['objective'],runtime=time.perf_counter()-begun,info=result['info'],
 scope='restricted_pool_retention_comparison_same_input_initial_budget',full_proven_optimal=False,
 recorded_64_control='pool22/control/result.json',new_official_upper_not_used_as_initial=True)
(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='pool_retention23_complete',active_process=None);(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
print(json.dumps(summary,indent=2))
