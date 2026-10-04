from pathlib import Path
import copy
import json
import os
import sys
import time

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_maximum6'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_e1_polish import reoptimize_e1
from models.routing.solvers.exact.multibatch_vehicle_retime import reoptimize_vehicle_retiming

OUT=HERE/'vehicle_after_pool6'
def write(path,value):
    path.parent.mkdir(exist_ok=True,parents=True)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n')

problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
initial=json.loads((OUT/'initial_result.json').read_text())
assert audit_solution(problem,initial)['valid']
c=json.loads((HERE/'checkpoint.json').read_text())
c.update(status='running_vehicle_after_pool6',active_process=os.getpid(),
    next_action='Wait for the new fixed-path timing comparison on CURRENT best paths, then independently validate an improvement before promotion.')
write(HERE/'checkpoint.json',c)
summary=[]
for name in ('fixed','flexible'):
    begun=time.perf_counter()
    if name=='fixed':
        result=reoptimize_e1(problem,copy.deepcopy(initial),time_limit=30,seed=42,
            retime_e2=True,verbose=True,diagnostics_path=OUT/(name+'.mps'))
    else:
        result=reoptimize_vehicle_retiming(problem,copy.deepcopy(initial),time_limit=30,seed=42,
            verbose=True,diagnostics_path=OUT/(name+'.mps'))
    audit=audit_solution(problem,result);assert audit['valid'],audit['errors']
    write(OUT/(name+'_result.json'),result);write(OUT/(name+'_audit.json'),audit)
    summary.append(dict(name=name,objective=audit['objective'],distances=audit['distances'],
        runtime=time.perf_counter()-begun,info=result['info']))
    write(OUT/'summary.json',summary)
c=json.loads((HERE/'checkpoint.json').read_text())
c.update(status='vehicle_after_pool6_complete',active_process=None,
    next_action='Read vehicle_after_pool6/summary.json. Independently reconstruct and project a better full schedule before replacing certificates. Restricted subproblem bounds are not global.')
write(HERE/'checkpoint.json',c)
print(json.dumps(summary,indent=2))
