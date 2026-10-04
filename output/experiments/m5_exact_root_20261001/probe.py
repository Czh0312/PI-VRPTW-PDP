import sys,json,time
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact import solver
from models.routing.solvers.exact.formulation import build_model
from models.routing.common.cycle.audit import audit_solution
rows=[]
paths=['output/experiments/m5_exact_primal_20260930/compact_5s/inputs/7/case.json',*[f'output/experiments/m5_exact_strengthening_20260930/larger_5s_verified/inputs/{i}/case.json' for i in range(2)]]
for path in paths:
 p=load_instance(*load_routing_case(path))
 for linear,sym in ((False,False),(False,True),(True,False),(True,True)):
  options=dict(linear_e2_links=linear,trip_symmetry=sym)
  with patch.object(solver,'build_model',side_effect=lambda *a,**kw:build_model(*a,**kw,**options)):
   r=solver.solve_exact(p,time_limit=5,seed=42)
  row=dict(orders=len(p.orders),options=options,info=r['info'],valid=audit_solution(p,r)['valid']);rows.append(row)
  (Path(__file__).parent/'probe.json').write_text(json.dumps(rows,indent=2,allow_nan=False))
  print({k:row[k] for k in ('orders','options','valid')}, {k:r['info'].get(k) for k in ('variables','constraints','build_time','objective','best_bound','mip_gap')},flush=True)
