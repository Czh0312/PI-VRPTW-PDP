from pathlib import Path
import sys,json
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
sys.path[:0]=[str(HERE),str(ROOT)]
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from validation_timing import verify_service
data=json.loads((HERE/'planned_inputs.json').read_text())
checks=[]
for case,initial in data['large']:
 problem=load_instance(*load_routing_case(case))
 for site in problem.satellites:
  if not any(o.satellite==site for o in problem.orders):continue
  from validation_timing import oracle
  groups=oracle(problem,site)
  checks.append(dict(case=case,satellite=site,groups=len(groups),envelopes=sum(len(g['clocks']) for g in groups)))
  (HERE/'oracle_progress.json').write_text(json.dumps(checks,indent=2)+'\n')
print('ALL LARGE CLOCK DOMAINS INDEPENDENTLY ENUMERATED',flush=True)
