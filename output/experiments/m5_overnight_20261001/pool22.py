from pathlib import Path
import sys,json,hashlib,os,time
from copy import deepcopy
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_subset19'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_route_pool import reoptimize_route_pool
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
manifest=json.loads((HERE/'manifest_pool22_input.json').read_text())['sha256']
for path,digest in manifest.items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
initial=json.loads((HERE/'pool22/initial_result.json').read_text());assert audit_solution(problem,initial)['valid']
old=json.loads((HERE/'pool_after_vehicle4/additional_columns.json').read_text())
new=json.loads((HERE/'pool22/physical_columns.json').read_text())
parameters=dict(time_limit=90,seed=42,max_columns=64,transfers=True,exchanges=True,recombinations=True,
 extra_slots_per_vehicle=1,max_passes=1,additional_columns_per_slot=64)
(HERE/'pool22/parameters.json').write_text(json.dumps(dict(**parameters,same_input=True,same_initial=True,
  comparison_sequential=True,scope='restricted_physical_E2_route_pool_full_upper_only'),indent=2)+'\n')
summaries=[]
for name in ('control','physical_proposals'):
 out=HERE/'pool22'/name;out.mkdir()
 c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='running_pool22_'+name,active_process=os.getpid());(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
 proposals=deepcopy(old)
 if name=='physical_proposals':proposals['S1']=new+proposals['S1']
 (out/'additional_columns.json').write_text(json.dumps(proposals,indent=2)+'\n')
 begun=time.perf_counter()
 result=reoptimize_route_pool(problem,initial,**parameters,additional_columns=proposals,diagnostics_dir=out/'models')
 audit=audit_solution(problem,result);assert audit['valid']
 (out/'result.json').write_text(json.dumps(result,indent=2)+'\n');(out/'audit.json').write_text(json.dumps(audit,indent=2)+'\n')
 summaries.append(dict(name=name,objective=audit['objective'],improved=audit['objective']<2235.9725985686455-1e-6,
   runtime=time.perf_counter()-begun,full_proven_optimal=False,full_proven_infeasible=False,
   info=result['info']))
 (HERE/'pool22/summary.json').write_text(json.dumps(summaries,indent=2)+'\n')
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='pool22_complete',active_process=None);(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
print(json.dumps(summaries,indent=2))
