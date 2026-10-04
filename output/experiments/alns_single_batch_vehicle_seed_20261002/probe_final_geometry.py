"""Descriptive follow-up: was the zero-option long check just too early?"""
from pathlib import Path
import json
import sys
import time

OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(OUT/'after_source'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.alns.partitioned_assignment import _tail_options
from models.routing.solvers.alns.neighborhoods import state_from_result

rows=[]
for index,job in enumerate(json.loads((OUT/'quality_manifest.json').read_text())['jobs']):
    if job['budget'] != 15:
        continue
    config,fleet=load_routing_case(job['case'])
    problem=load_instance({**config,'model':2},fleet)
    for source in ('before_source','after_source'):
        raw=json.loads((OUT/'quality'/('%03d_%s.json'%(index,source))).read_text())
        result=raw['result']
        audit=audit_solution(problem,result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        started=time.perf_counter()
        options=_tail_options(problem,result['assignment'],state_from_result(problem,result),started+5)
        assert options is not None
        rows.append(dict(index=index,seed=job['seed'],source=source,
                         final_geometric_options=len(options),seconds=time.perf_counter()-started,
                         runtime_options=result['info'].get('assignment_route_seed_options')))
(OUT/'final_geometry_diagnosis.json').write_text(json.dumps(rows,indent=2))
print(json.dumps(rows,indent=2))
