"""Read-only cut-free robustness audit of previously certified complete masters."""
from pathlib import Path
import json,hashlib,datetime,gurobipy as gp
HERE=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
plan=json.loads((HERE/'protected_master_audit_plan.json').read_text());assert sha(Path(__file__))==plan['script_sha256']
answers=[]
for spec in plan['models']:
 f=Path(spec['path']);assert sha(f)==spec['sha256']
 m=gp.read(str(f))
 try:
  m.Params.OutputFlag=0;m.Params.Threads=1;m.Params.Seed=53;m.Params.Cuts=0;m.Params.DualReductions=0;m.Params.IntFeasTol=m.Params.FeasibilityTol=1e-9;m.Params.MIPGap=m.Params.MIPGapAbs=0;m.Params.TimeLimit=10;m.optimize()
  row=dict(label=spec['label'],status=m.Status,raw_claim=spec['raw_claim'],certified_claim=spec['certified_claim'],bound=m.ObjBound,objective=m.ObjVal if m.SolCount else None,runtime=m.Runtime,sol_count=m.SolCount)
  if m.SolCount:
   vs=m.getVars();x={v.VarName:(round(v.X) if v.VType!='C' else v.X) for v in vs}
   def act(e):return sum(e.getCoeff(i)*x[e.getVar(i).VarName] for i in range(e.size()))
   def vio(a,s,b):return max(0,a-b) if s=='<' else max(0,b-a) if s=='>' else abs(a-b)
   b=max(max(0,v.LB-x[v.VarName],x[v.VarName]-v.UB) for v in vs)
   linear=max(vio(act(m.getRow(c)),c.Sense,c.RHS) for c in m.getConstrs());iv=[]
   for c in m.getGenConstrs():
    v,k,e,s,z=m.getGenConstrIndicator(c)
    if x[v.VarName]==k:iv.append(vio(act(e),s,z))
   objective=m.ObjCon+sum(v.Obj*x[v.VarName] for v in vs)
   row.update(rounded_objective=objective,max_rounded_bound_violation=b,max_rounded_linear_violation=linear,max_rounded_active_indicator_violation=max(iv) if iv else 0,solver_max_violation=m.MaxVio)
   row['counterexample_to_certified_claim']=objective<spec['certified_claim']-1e-5 and max(b,linear,max(iv) if iv else 0)<=1e-7
   if row['counterexample_to_certified_claim']:
    m.write(str(HERE/(spec['label']+'_counterexample.sol')))
    for v in vs:v.LB=v.UB=x[v.VarName]
    m.optimize();row['fixed_rounded_replay_status']=m.Status;row['fixed_rounded_objective']=m.ObjVal if m.SolCount else None
   else:row['fully_reproduces_raw_claim']=m.Status==gp.GRB.OPTIMAL and abs(m.ObjVal-spec['raw_claim'])<=1e-5
  answers.append(row);print(row,flush=True)
 finally:m.dispose()
 assert sha(f)==spec['sha256']
(HERE/'protected_master_audit.json').write_text(json.dumps(dict(status='EXISTING_CERTIFICATE_ROBUSTNESS_AUDIT',time_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),cases=answers,all_source_models_unchanged=True,no_new_alns_or_algorithm_experiment=True),indent=2)+'\n')
