from pathlib import Path
import json
import os
import sys
import time

HERE=Path(__file__).resolve().parent
SNAPSHOT=HERE/'source_snapshot_vehicle4'
OUT=HERE/'pool_after_vehicle4'
sys.path.insert(0,str(SNAPSHOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_route_pool import reoptimize_route_pool


def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def checkpoint(status,**extra):
    c=json.loads((HERE/'checkpoint.json').read_text());c.update({'status':status,'active_process':os.getpid(),**extra})
    write(HERE/'checkpoint.json',c)


problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
initial=json.loads((OUT/'initial_result.json').read_text())
columns=json.loads((OUT/'additional_columns.json').read_text())
assert audit_solution(problem,initial)['valid']
checkpoint('running_pool_after_vehicle4',next_action='Wait for this 90-second path-pool trial. Inspect pool_after_vehicle4/result.json and audit.json. Any restricted bound is not global; independently verify any improved upper before promotion.')
begun=time.perf_counter()
result=reoptimize_route_pool(problem,initial,time_limit=90,seed=42,max_columns=64,
    transfers=True,exchanges=True,recombinations=True,extra_slots_per_vehicle=1,max_passes=1,
    additional_columns=columns,additional_columns_per_slot=32,diagnostics_dir=OUT/'models',verbose=True)
audit=audit_solution(problem,result);assert audit['valid']
write(OUT/'result.json',result);write(OUT/'audit.json',audit)
write(OUT/'summary.json',dict(objective=audit['objective'],distances=audit['distances'],
    full_audit=True,runtime=time.perf_counter()-begun,info=result['info']))
checkpoint('pool_after_vehicle4_complete',active_process=None,
    next_action='Read pool_after_vehicle4/summary.json. If the upper improves, validate the candidate through independent physical/E1 model projections and directed-route accounting before replacing best_full_result.json. If unchanged, save the negative result and consider flexible vehicle assignment for complete-cover route combinations. Never use restricted pool ObjBound as a global lower.')
print(json.dumps(dict(objective=audit['objective'],runtime=time.perf_counter()-begun,info=result['info']),indent=2))
