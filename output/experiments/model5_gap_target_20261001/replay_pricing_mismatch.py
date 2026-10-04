"""Replay the captured numerical false negative without relaxing validation."""
import json
from pathlib import Path
import gurobipy as gp
from gurobipy import GRB

HERE = Path(__file__).resolve().parent
DEBUG = HERE/'pricing_mismatch_debug'
captured = json.loads((DEBUG/'mismatch.json').read_text())
m = gp.read(str(DEBUG/'mismatch.mps'))
try:
    m.Params.OutputFlag = 0
    m.Params.Threads = 1
    m.Params.IntFeasTol = m.Params.FeasibilityTol = 1e-9
    m.Params.IntegralityFocus = 1
    m.Params.TimeLimit = 15
    m.Params.MIPGap = m.Params.MIPGapAbs = 0
    original = {v['name']: v for v in captured['variables']}
    residuals = []
    for v in m.getVars():
        row = original[v.VarName]
        v.Start = row['value']
        if v.VType == GRB.BINARY:
            rounded = round(row['value'])
            residuals.append(abs(row['value']-rounded))
            v.LB = v.UB = rounded
    m.optimize()
    assert m.Status == GRB.OPTIMAL
    assert abs(m.ObjVal-captured['reduced']) < 1e-6
    assert abs(captured['difference']) > 1e-5
    fixed = dict(status=int(m.Status), objective=m.ObjVal,
        route_replay_reduced_cost=captured['reduced'])
    summary = dict(original_objective=captured['objective'],
        original_reduced_cost=captured['reduced'], difference=captured['difference'],
        maximum_binary_residual=max(residuals), rounded_route_replay=fixed,
        conclusion='Original default tolerances accepted near-integer flow values, producing a false negative reduced cost. Tightened solver tolerances retain the original independent cost check.')
    (HERE/'pricing_mismatch_replay.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(summary)
finally:
    m.dispose()
