"""Freeze completed paired evidence and the separately projected best record."""
import hashlib,json,re
from pathlib import Path

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
def read(p):return json.loads(p.read_text())
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

plan=read(HERE/'plan.json');validation=read(HERE/'validation.json');best=read(HERE/'best_certified.json')
assert validation['status']==best['status']=='VERIFIED'
assert len(validation['case_results'])==len(plan['jobs'])==2
assert len(read(HERE/'summary.json'))==2
assert all(row['failure'] is None for row in read(HERE/'summary.json'))
assert all(row['wall_seconds']<=660.5 for row in validation['case_results'])
for row in validation['case_results']:
    assert row['gap_percent']>5 and not row['target_reached']
assert best['target_reached'] and best['gap_percent']<5 and best['upper']<best['previous_upper']
assert best['same_coefficients_verified'] and best['historical_master_projection']['full_schedule_projection']
assert best['current_cold_validation_sha256']==sha(HERE/'validation.json')
assert best['primal_source']=='full_candidate_43'
assert abs(best['gap_percent']/100-(best['upper']-best['lower'])/best['upper'])<1e-12
tests=re.search(r'(?m)^188 passed in ([0-9.]+)s$',(HERE/'pytest_final.log').read_text());assert tests

production=read(HERE/'production_manifest.json')
assert production['new_features_opt_in']
for name,digest in production['production_files_sha256'].items():
    assert sha(ROOT/name)==digest
    if name.startswith('models/'):
        assert plan['source_sha256']['candidate_source'][name]==digest
for directory,hashes in plan['source_sha256'].items():
    for name,digest in hashes.items():assert sha(HERE/directory/name)==digest
for name,digest in plan['input_sha256'].items():assert sha(HERE/name)==digest
assert sha(HERE/'run.py')==plan['runner_sha256']
baseline=plan['source_sha256']['baseline_source'];candidate=plan['source_sha256']['candidate_source']
assert {k for k in baseline if baseline[k]!=candidate[k]}==set(plan['declared_source_changes'])

old=HERE.parent/'model5_gap_round6_20261001';old_manifest=read(old/'final_verification.json')
for origin in read(HERE/'validation_sources.json')['origins'].values():
    path=Path(origin['source'])
    assert sha(path)==origin['sha256']==old_manifest['artifacts_sha256'][path.name]
assert sha(old/'validation_final.json')==best['original_proof_sha256']==old_manifest['artifacts_sha256']['validation_final.json']
assert sha(old/'conjunction/full_joint.mps.gz')==best['historical_joint_mps_sha256']==old_manifest['artifacts_sha256']['conjunction/full_joint.mps.gz']
input_manifest=Path(plan['prior_input_manifest'])
assert sha(input_manifest)==plan['prior_input_manifest_sha256']
original=read(input_manifest)['artifact_sha256']
for row in plan['input_bindings']:
    path=Path(row['source'])
    assert sha(path)==row['source_sha256']==sha(Path(row['frozen']))==original[str(path.relative_to(input_manifest.parent))]

report=ROOT/'docs/model5_gap_round7_20261001.md'
for target in re.findall(r'\]\(([^)]+)\)',report.read_text()):
    if target.endswith('final_verification.json'):continue
    assert (report.parent/target).exists(),target
artifacts={str(p.relative_to(HERE)):sha(p) for p in sorted(HERE.rglob('*'))
           if p.is_file() and '__pycache__' not in p.parts and p.name!='final_verification.json'}
out=dict(status='VERIFIED',target='BEST_RECORD_IMPROVED_COLD_TARGET_NOT_REACHED',
         regression_tests=188,regression_seconds=float(tests[1]),
         cold_cases=validation['case_results'],best_certified={k:best[k] for k in ['upper','lower','gap_percent','target_reached']},
         whole_cycle_audits=validation['whole_cycle_audits'],
         independently_replayed_columns=validation['independently_replayed_columns'],
         repaired_dual_checks=validation['repaired_dual_checks'],
         cold_model_checks=len(validation['model_checks']),historical_master_projection=True,
         automatic_selection_checks=validation['automatic_selection_checks'],
         production_files_sha256=production['production_files_sha256'],document_sha256=sha(report),
         artifact_count=len(artifacts),artifacts_sha256=artifacts,
         interpretation='Only the cross-run best is within 5%; both 660s cold runs missed it. New features remain opt-in. One development configuration and one seed; no universal stability claim.')
(HERE/'final_verification.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print({k:out[k] for k in ['status','target','regression_tests','cold_model_checks','artifact_count','best_certified']})
