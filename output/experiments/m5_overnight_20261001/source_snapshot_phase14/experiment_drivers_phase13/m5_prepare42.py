from pathlib import Path
import json,hashlib
H=Path('/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001')
s=(H/'validators_phase13/physical.py').read_text().replace("assert cert['status']==3 and cert['subproblem_infeasible']","assert cert['status']==2 and cert['subproblem_optimal'] and not cert['subproblem_infeasible']")
s=s.replace("changed.optimize();assert changed.Status==3","changed.Params.MIPGap=changed.Params.MIPGapAbs=0;changed.optimize();assert changed.Status==2 and changed.ObjBound+1e-6>=cert['bound']")
s=s.replace('conditional_infeasibility_independently_replayed=True','conditional_cost_bound_independently_replayed=True,raw_bound=changed.ObjBound,certified_bound=cert[\'bound\']')
(H/'validators_phase13/physical_cost.py').write_text(s)
