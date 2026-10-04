"""Exploratory integrality on previously exported COMPLETE route-cover LPs."""
import gzip
import json
import math
from pathlib import Path
import time
import gurobipy as gp
from gurobipy import GRB

here = Path(__file__).resolve().parent
previous = here.parent/'m5_enumerated_pricing_20261001/final_60s'
report = []
for case in range(3):
    payload = json.loads((previous/f'{case:02d}_42_hybrid.json').read_text())
    initial = json.loads(Path(payload['job']['initial_result']).read_text())
    rows = []
    for index, site in enumerate(payload['certificate']['sites']):
        prefix = previous/f'{case:02d}_42_hybrid_pricing_models/site_{index}_best_pricing'
        with gzip.open(str(prefix)+'.json.gz','rt') as stream:
            universe = json.load(stream)
        assert universe['complete']
        columns = [c for g in universe['groups'] for c in g['columns']]
        selected = {frozenset((e['order_id'],e['kind']) for e in t['events']) for t in initial['e2_trips'] if t['satellite_id']==site['satellite']}
        model = gp.read(str(prefix)+'_master.mps.gz')
        try:
            model.Params.OutputFlag=0
            model.Params.Threads=1
            model.Params.Seed=42
            model.Params.MIPGap=model.Params.MIPGapAbs=0
            model.Params.TimeLimit=15
            for v,c in zip(model.getVars(),columns):
                v.VType=GRB.BINARY
                v.UB=1
                v.Start=int(frozenset(tuple(k) for k in c['tasks']) in selected)
            begun=time.perf_counter();model.optimize()
            row=dict(site=site['satellite'],lp=site['iterations'][0]['master_objective'],bound=model.ObjBound,
                objective=model.ObjVal if model.SolCount else None,status=int(model.Status),nodes=model.NodeCount,
                runtime=time.perf_counter()-begun,columns=len(columns))
            rows.append(row);print(json.dumps(row),flush=True)
        finally:
            model.dispose()
    e1=payload['certificate']['e1_bound']
    bound=e1+math.fsum(r['bound'] for r in rows)
    report.append(dict(customers=payload['job']['customers'],e1=e1,bound=bound,
        gap=(payload['job']['frozen_upper_bound']-bound)/payload['job']['frozen_upper_bound'],sites=rows))
    (here/'probe.json').write_text(json.dumps(report,indent=2)+'\n')
    print('combined '+json.dumps(report[-1]),flush=True)
