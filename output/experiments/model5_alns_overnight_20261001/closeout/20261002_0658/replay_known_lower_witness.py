"""Bounded read-only replay audit of an already-exported certificate model."""
from pathlib import Path
import hashlib,json,datetime,sys
import gurobipy as gp
HERE=Path(__file__).resolve().parent
plan=json.loads((HERE/'audit_plan.json').read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(Path(__file__))==plan['script_sha256']
master=Path(plan['master']);witness=Path(plan['witness']);assert sha(master)==plan['master_sha256'] and sha(witness)==plan['witness_sha256']
prior=Path(plan['prior_evidence_seal']);seal=json.loads(prior.read_text());assert sha(prior)==plan['prior_evidence_seal_sha256']
for relative,digest in seal['artifact_sha256'].items():assert sha(prior.parent/relative)==digest
answers=[]
for item in plan['audits']:
 m=gp.read(str(master))
 try:
  m.Params.OutputFlag=0;m.Params.Threads=1;m.Params.Seed=item['seed'];m.Params.MIPGap=m.Params.MIPGapAbs=0;m.Params.DualReductions=0;m.Params.IntFeasTol=m.Params.FeasibilityTol=1e-9;m.Params.TimeLimit=10
  if item['known_lower_start']:m.read(str(witness))
  if item['disable_cuts']:m.Params.Cuts=0
  m.optimize()
  row=dict(audit=item,status=m.Status,bound=m.ObjBound,objective=m.ObjVal if m.SolCount else None,runtime=m.Runtime,original_reported_raw=plan['original_reported_raw'],quality_max_violation=m.MaxVio if m.SolCount else None,interpretation='Diagnostic of bound inconsistency only. Not a new ALNS run, algorithm comparison, replacement certificate, or best-record promotion.')
  answers.append(row);print(row,flush=True)
 finally:m.dispose()
for relative,digest in seal['artifact_sha256'].items():assert sha(prior.parent/relative)==digest
(HERE/'master_audit.json').write_text(json.dumps(dict(status='DIAGNOSTIC_REPLAY_ONLY',time_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),cases=answers,gurobi_version=list(gp.gurobi.version()),original_sealed_artifacts_unchanged=True,model_unchanged=True,no_promotion=True),indent=2)+'\n')
