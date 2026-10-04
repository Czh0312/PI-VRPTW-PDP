from pathlib import Path
import hashlib,json,time
import gurobipy as gp
HERE=Path(__file__).resolve().parent.parent
started=time.perf_counter()
manifest=json.loads((HERE/'manifest_focus15_input.json').read_text())['sha256']
for p,digest in manifest.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==digest
frozen=json.loads((HERE/'manifest_phase6_final.json').read_text())['sha256']
source=HERE/'pairs12/master/no_good.mps.gz'
source_key=str(source.relative_to(Path.cwd()))
sha=hashlib.sha256(source.read_bytes()).hexdigest();assert sha==frozen[source_key]
proof_path=HERE/'pairs12/master/independent_validation.json'
assert hashlib.sha256(proof_path.read_bytes()).hexdigest()==frozen[str(proof_path.relative_to(Path.cwd()))]
proof=json.loads(proof_path.read_text());assert proof['matrix']['original_matrix_exactly_identical']
trials=json.loads((HERE/'focus15/master/summary.json').read_text())
assert {r['mip_focus'] for r in trials}=={0,3}
for trial in trials:
    assert trial['matrix_sha256']==sha
    assert hashlib.sha256((HERE/'focus15/master'/(trial['name']+'.mps.gz')).read_bytes()).hexdigest()==sha
projection=json.loads((HERE/'focus15/master/full_projection.json').read_text())
assert projection['full_schedule_projection'] and abs(projection['objective']-2235.9725985686455)<1e-6
old=json.loads((HERE/'combined_certificate.json').read_text())['lower_bound']
best=max(trials,key=lambda r:r['candidate_global_bound'])
replay=None;supported=False
if best['candidate_global_bound']>old+1e-6:
    model=gp.read(str(source))
    try:
        model.read(str(HERE/'focus15/master'/(best['name']+'.sol.gz')))
        model.Params.Threads=1;model.Params.Seed=42;model.Params.Presolve=1
        model.Params.MIPFocus=best['mip_focus'];model.Params.DualReductions=0
        model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9
        model.Params.MIPGap=model.Params.MIPGapAbs=0;model.Params.TimeLimit=120
        model.Params.BestBdStop=best['candidate_global_bound'];model.optimize()
        bound=float(model.ObjBound);supported=bound>=best['candidate_global_bound']-1e-7
        replay=dict(status=int(model.Status),raw_bound=bound,candidate_supported=supported,
            runtime=model.Runtime,full_proven_optimal=False)
    finally:model.dispose()
result=dict(matrix_identical_bytes_to_verified_pairs12=True,matrix_sha256=sha,
    previous_coefficient_validation=str(proof_path),full_projection=projection,
    best_variant=best['name'],candidate_global_lower=best['candidate_global_bound'],old_global_lower=old,
    independent_replay=replay,promote_supported=supported,full_proven_optimal=False,
    runtime=time.perf_counter()-started)
(HERE/'focus15/master/independent_validation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
