import json
import math
from pathlib import Path
import sys
import time
import gurobipy as gp

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_joint_inventory import set_joint_projection
from warm_context import context_for_export

OLD=HERE.parent/'m5_route_timing_20261001'
payload=json.loads((OLD/'verified_120s/02_42_timed.json').read_text())
problem=load_instance(*load_routing_case(payload['job']['case']))
initial=json.loads((OLD/'best_full_result.json').read_text())
audit=audit_solution(problem,initial)
assert audit['valid']
begun=time.perf_counter()
directory=OLD/'verified_120s/02_42_timed_models'
model=gp.read(str(directory/'joint/joint_inventory.mps.gz'))
model.Params.Threads=1
model.Params.MIPGap=model.Params.MIPGapAbs=0
model.Params.FeasibilityTol=model.Params.IntFeasTol=1e-9
model.Params.Presolve=1
model.Params.Seed=42
context=context_for_export(problem,model,directory)
projection=set_joint_projection(problem,context,initial)
lp=model.relax()
lp.Params.Threads=1
lp.Params.Method=1
lp.Params.TimeLimit=25
lp.optimize()
assert lp.Status==gp.GRB.OPTIMAL
lower=lp.ObjVal-1e-7*max(1,abs(lp.ObjVal))
gap=audit['objective']-lower
fixed=0
for v,r in zip(model.getVars(),lp.getVars()):
    assert v.VarName==r.VarName
    if v.VarName.startswith('joint_route_1_'):
        v.VType=gp.GRB.BINARY
        if r.X < 1e-9 and r.RC > gap+1e-5 and v.Start < .5:
            v.UB=0;fixed+=1
lp_info=dict(status=lp.Status,objective=lp.ObjVal,lower=lower,gap=gap,fixed=fixed,runtime=lp.Runtime)
lp.dispose()
model.update()
model.Params.Heuristics=0
model.Params.MIPFocus=2
model.Params.Cuts=1
model.Params.Cutoff=audit['objective']+1e-5
model.Params.TimeLimit=120
model.optimize()
raw=float(model.ObjBound)
bound=max(payload['bound'],max(0,raw-1e-7*max(1,abs(raw)))) if math.isfinite(raw) else payload['bound']
out=dict(lp=lp_info,projection=projection,status=model.Status,raw_bound=raw,bound=bound,
    objective=model.ObjVal if model.SolCount else None,upper=audit['objective'],gap=(audit['objective']-bound)/audit['objective'],
    nodes=model.NodeCount,wall_seconds=time.perf_counter()-begun)
(HERE/'probe_reduced_cost.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out),flush=True)
model.dispose()
