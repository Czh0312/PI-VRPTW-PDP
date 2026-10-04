from pathlib import Path
import sys,json,time,hashlib
import gurobipy as gp
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_subset19'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_physical_site import physical_site_certificate
out=HERE/'cover_partition20'
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
tasks=[(o.id,k) for o in problem.orders if o.satellite=='S1' for k in ('d','p')]
m=gp.read(str(HERE/'inputs/S1_integer_cover.mps.gz'))
m.Params.Threads=1;m.Params.Seed=42;m.Params.TimeLimit=30
m.Params.MIPGap=m.Params.MIPGapAbs=0
m.optimize();assert m.Status==2
mapping={}
for i,key in enumerate(tasks):
 e=m.getRow(m.getConstrByName('cover_'+str(i)))
 for j in range(e.size()):
  assert e.getCoeff(j)==1
  mapping.setdefault(e.getVar(j).index,[]).append(key)
selected=[dict(variable=v.VarName,cost=v.Obj,tasks=mapping[v.index]) for v in m.getVars() if v.X>.5]
groups=[r['tasks'] for r in selected]
assert len({tuple(k) for g in groups for k in g})==len(tasks)
result=dict(status=int(m.Status),raw_cover_objective=m.ObjVal,selected=selected,trip_groups=groups,
    full_proven_optimal=False,full_proven_infeasible=False,scope='complete_E2_cover_optimal_task_partition')
(out/'cover_point.json').write_text(json.dumps(result,indent=2)+'\n');m.write(str(out/'cover_point.sol.gz'));m.dispose()
cert=physical_site_certificate(problem,'S1',time_limit=30,seed=42,trip_groups=groups,
    cost_floor=451.71429479447573,diagnostics_dir=out,diagnostic_stem='physical')
(out/'physical.json').write_text(json.dumps(cert,indent=2)+'\n')
print(json.dumps({k:v for k,v in cert.items() if k!='projection_proof'},indent=2))
