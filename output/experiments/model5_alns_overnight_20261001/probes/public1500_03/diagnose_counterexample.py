"""Diagnostic of the lower exported-master solution; no bound promotion."""
from pathlib import Path
import json,hashlib,gurobipy as gp
Q=Path(__file__).resolve().parent
reg=json.loads((Q/'counterexample_registration.json').read_text());assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest()==reg['script_sha256']
m=gp.read(str(Q/'seed53_public1500_models/conditional/master_0.mps.gz'))
try:
 m.Params.OutputFlag=0;m.Params.Threads=1;m.Params.Seed=43;m.Params.MIPGap=m.Params.MIPGapAbs=0;m.Params.DualReductions=0;m.Params.IntFeasTol=m.Params.FeasibilityTol=1e-9;m.Params.TimeLimit=30;m.optimize()
 assert m.Status==gp.GRB.OPTIMAL
 original=dict(objective=m.ObjVal,bound=m.ObjBound,solver_max_violation=m.MaxVio,solver_integrality_violation=m.IntVio)
 vs=m.getVars();x={v.VarName:(round(v.X) if v.VType!='C' else v.X) for v in vs}
 def activity(expr):return sum(expr.getCoeff(i)*x[expr.getVar(i).VarName] for i in range(expr.size()))
 def violation(a,s,b):return max(0,a-b) if s=='<' else max(0,b-a) if s=='>' else abs(a-b)
 bound=max(max(0,v.LB-x[v.VarName],x[v.VarName]-v.UB) for v in vs)
 rows=[(violation(activity(m.getRow(c)),c.Sense,c.RHS),c.ConstrName) for c in m.getConstrs()]
 indicators=[]
 for c in m.getGenConstrs():
  v,b,e,s,r=m.getGenConstrIndicator(c)
  if x[v.VarName]==b:indicators.append((violation(activity(e),s,r),c.GenConstrName))
 obj=m.ObjCon+sum(v.Obj*x[v.VarName] for v in vs)
 m.write(str(Q/'seed43_lower_master_solution.sol'))
 # Projection all integers rounded and all continuous coordinates fixed.
 for v in vs:v.LB=v.UB=x[v.VarName]
 m.optimize()
 d=dict(status='DIAGNOSTIC_ONLY',original=original,independent_rounded_objective=obj,max_rounded_bound_violation=bound,max_rounded_linear_violation=max(rows),max_rounded_active_indicator_violation=max(indicators) if indicators else None,fixed_rounded_replay_status=m.Status,fixed_rounded_objective=m.ObjVal if m.SolCount else None,original_claim_not_promoted=True)
 (Q/'counterexample_diagnostic.json').write_text(json.dumps(d,indent=2)+'\n');print(d)
finally:m.dispose()
