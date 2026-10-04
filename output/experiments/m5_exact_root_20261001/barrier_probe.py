import sys,json
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'probe_15s/source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact import solver
from models.routing.solvers.exact.formulation import build_model
def built(*a,**kw):
 m,ctx=build_model(*a,**kw)
 m.Params.Method=2
 return m,ctx
for i in (1,2):
 problem=load_instance(*load_routing_case(str(ROOT/f'probe_15s/inputs/{i}/case.json')))
 with patch.object(solver,'build_model',side_effect=built):
  result=solver.solve_exact(problem,time_limit=15,seed=42,verbose=True)
 payload=dict(case=str(ROOT/f'probe_15s/inputs/{i}/case.json'),source_manifest=str(ROOT/'probe_15s/manifest.json'),parameters=dict(Method=2,Threads=1),budget=15,seed=42,result=result,independent_audit=audit_solution(problem,result))
 (ROOT/f'barrier_{len(problem.orders)}.json').write_text(json.dumps(payload,indent=2,allow_nan=False))
 print('BARRIER_RESULT',len(problem.orders),result['info'],flush=True)
