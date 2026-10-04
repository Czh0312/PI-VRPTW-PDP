"""Same original input/incumbent and budget; sequential physical fleet trials."""
from pathlib import Path
import hashlib
import json
import os
import sys
import time

SNAPSHOT=Path(__file__).resolve().parent
HERE=SNAPSHOT.parent
OUT=HERE/'vehicle_retime4'
OUT.mkdir(exist_ok=True)
sys.path.insert(0,str(SNAPSHOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_e1_polish import reoptimize_e1
from models.routing.solvers.exact.multibatch_vehicle_retime import reoptimize_vehicle_retiming


def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def checkpoint(status,**extra):
    value=json.loads((HERE/'checkpoint.json').read_text())
    value.update({'status':status,'active_process':os.getpid(),**extra})
    write(HERE/'checkpoint.json',value)


problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
initial=json.loads((HERE/'inputs/initial_result.json').read_text())
assert audit_solution(problem,initial)['valid']
results=[]
for seed in (42,43,44):
    for variant in ('fixed_vehicle','flexible_vehicle'):
        job=f'{seed}_{variant}'
        checkpoint('running_vehicle_retiming_'+job,source_snapshot=str(SNAPSHOT),
            next_action='Wait for this sequential trial worker; inspect vehicle_retime4/summary.json. Do not start another solver job concurrently.')
        begun=time.perf_counter()
        path=OUT/(job+'.mps.gz')
        if variant=='fixed_vehicle':
            result=reoptimize_e1(problem,initial,time_limit=30,seed=seed,retime_e2=True,diagnostics_path=path)
        else:
            result=reoptimize_vehicle_retiming(problem,initial,time_limit=30,seed=seed,diagnostics_path=path)
        audit=audit_solution(problem,result)
        assert audit['valid']
        write(OUT/(job+'_result.json'),result)
        write(OUT/(job+'_audit.json'),audit)
        row=dict(job=job,variant=variant,seed=seed,time_limit=30,wall_seconds=time.perf_counter()-begun,
            input_initial_sha256=hashlib.sha256((HERE/'inputs/initial_result.json').read_bytes()).hexdigest(),
            original_upper=initial['info']['objective'],objective=audit['objective'],
            distances=audit['distances'],full_audit=True,info=result['info'])
        results.append(row)
        write(OUT/'summary.json',results)
        print(json.dumps(row,ensure_ascii=False),flush=True)
checkpoint('vehicle_retiming_trials_complete',active_process=None,
    next_action='Read vehicle_retime4/summary.json; independently check all new fleet/rank/travel/supply/common-return rows in the exported flexible models and reconstruct output schedules directly from MPS solutions. Retain the previously verified upper 2238.1568127099035 until any better output is independently verified. The known lower is 2235.4231373718553; restricted subproblem bounds are not global.')
