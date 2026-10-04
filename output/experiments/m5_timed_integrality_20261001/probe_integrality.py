import json
import math
from pathlib import Path
import time
import gurobipy as gp

HERE = Path(__file__).resolve().parent
OLD = HERE.parent/'m5_route_timing_20261001'
payload = json.loads((OLD/'verified_120s/02_42_timed.json').read_text())
begun = time.perf_counter()
model = gp.read(str(OLD/'verified_120s/02_42_timed_models/joint/joint_inventory.mps.gz'))
model.Params.Threads = 1
model.Params.MIPGap = model.Params.MIPGapAbs = 0
model.Params.FeasibilityTol = model.Params.IntFeasTol = 1e-9
model.Params.Presolve = 1
model.Params.Seed = 42
model.Params.Heuristics = 0
model.Params.TimeLimit = 50
model.optimize()
base = dict(status=model.Status, bound=model.ObjBound,
            objective=model.ObjVal if model.SolCount else None, runtime=model.Runtime)
if model.SolCount:
    for v in model.getVars(): v.Start = v.X
columns = [v for v in model.getVars() if v.VarName.startswith('joint_route_1_')]
for v in columns: v.VType = gp.GRB.BINARY
model.update()
model.Params.Heuristics = .05
model.Params.MIPFocus = 3
model.Params.TimeLimit = 120
model.optimize()
raw = float(model.ObjBound)
bound = max(payload['bound'], max(0, raw-1e-7*max(1, abs(raw)))) if math.isfinite(raw) else payload['bound']
result = dict(base=base, status=model.Status, raw_bound=raw, bound=bound,
    objective=model.ObjVal if model.SolCount else None, promoted_columns=len(columns),
    upper=payload['upper_bound'], gap=(payload['upper_bound']-bound)/payload['upper_bound'],
    nodes=model.NodeCount, wall_seconds=time.perf_counter()-begun)
(HERE/'probe_integrality.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result),flush=True)
model.dispose()
