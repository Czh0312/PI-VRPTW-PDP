from pathlib import Path
import json,hashlib,time
import gurobipy as gp
HERE=Path(__file__).resolve().parent.parent;out=HERE/'cover26';model_path=out/'models/route_pool.mps.gz'
proof=json.loads((out/'matrix_validation.json').read_text())
assert proof['canonical_fixed_cover_projection_complete'] and proof['original_E1_attributes_and_matrix_unchanged']
started=time.perf_counter();model=gp.read(str(model_path))
try:
 model.Params.Threads=1;model.Params.Seed=43;model.Params.TimeLimit=60
 model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9;model.optimize()
 assert model.Status==gp.GRB.INFEASIBLE
 model.computeIIS();model.write(str(out/'conditional_conflict.ilp'))
 indicators=[v.GenConstrName for v in model.getGenConstrs() if v.IISGenConstr]
 rows=[v.ConstrName for v in model.getConstrs() if v.IISConstr]
 bounds=[dict(name=v.VarName,lower=bool(v.IISLB),upper=bool(v.IISUB)) for v in model.getVars() if v.IISLB or v.IISUB]
 result=dict(matrix_sha256=hashlib.sha256(model_path.read_bytes()).hexdigest(),status=int(model.Status),
  independently_replayed_conditional_infeasible=True,all_original_rules_retained=True,
  conditions='All 13 ordered E2 paths of frozen 20-customer cover held fixed; every real vehicle can schedule up to min(site path count, original trip limit).',
  full_proven_infeasible=False,global_no_good_installed=False,
  compressed_group_cut_warning='Task-set projection may replace an actual ordered path by another path. This ordered-cover infeasibility cannot be used as an unordered-group no-good without a separate all-permutations oracle.',
  iis_minimal=bool(model.IISMinimal),iis_linear_rows=rows,iis_indicators=indicators,iis_bounds=bounds,runtime=time.perf_counter()-started)
finally:model.dispose()
(out/'independent_validation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if not k.startswith('iis_')},indent=2))
