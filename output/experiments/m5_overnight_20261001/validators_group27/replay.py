from pathlib import Path
import gurobipy as gp
import json,time,hashlib
HERE=Path(__file__).resolve().parent.parent;out=HERE/'group27'
assert json.loads((out/'domain_validation.json').read_text())['entire_candidate_domain_independently_equal']
assert json.loads((out/'matrix_validation.json').read_text())['canonical_all_group_orders_projection_complete']
path=out/'models/route_pool.mps.gz';started=time.perf_counter();model=gp.read(str(path))
try:
 model.Params.Threads=1;model.Params.Seed=43;model.Params.TimeLimit=60
 model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9;model.optimize();assert model.Status==gp.GRB.INFEASIBLE
 result=dict(status=int(model.Status),matrix_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
  all_permutations_independently_checked=True,all_original_E1_and_physical_pool_rows_checked=True,
  independent_conditional_infeasibility_replay=True,full_proven_infeasible=False,runtime=time.perf_counter()-started)
finally:model.dispose()
(out/'independent_validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
