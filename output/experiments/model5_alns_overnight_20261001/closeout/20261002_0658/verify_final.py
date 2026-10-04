"""Read-only final evidence verification; never reruns or overwrites solver outputs."""
from pathlib import Path
import hashlib,json
HERE=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
seal=read(HERE/'final_verification.json')
assert seal['status']=='CLOSEOUT_VERIFIED_WITH_UNRESOLVED_ISSUES'
for name,digest in seal['artifact_sha256'].items():assert sha(HERE/name)==digest,name
for name,digest in seal['external_sha256'].items():assert sha(Path(name))==digest,name
checkpoint=read(HERE/'checkpoint_final.json');assert checkpoint['running'] is None and checkpoint['tool_session_id'] is None
assert checkpoint['automation_closeout']['last_read_status']=='PAUSED'
index=read(Path(checkpoint['evidence_index']))
assert index['best_cross_run']==checkpoint['best_full']
assert index['best_native_public1500']==checkpoint['best_native_public1500_pipeline']
full=partial=unexecuted=0
for row in index['experiments']:
 if row['status']=='VERIFIED_SEALED':
  manifest=Path(row['final_seal']);v=read(manifest);assert v['status']=='VERIFIED';full+=1
 elif row['status']=='SEALED_REFERENCE_ONLY_ORIGINAL_MASTER_FAILED':
  manifest=Path(row['partial_evidence_seal']);v=read(manifest);assert v['status']=='SEALED_REFERENCE_ONLY_WITH_ORIGINAL_MASTER_FAILURE';partial+=1
  assert not (manifest.parent/'validation.json').exists() and not (manifest.parent/'final_verification.json').exists()
 elif row['status']=='REGISTERED_NOT_EXECUTED':
  unexecuted+=1;assert not (Path(row['plan']).parent/'run_started.json').exists();continue
 else:raise AssertionError(row['status'])
 for name,digest in v.get('artifact_sha256',v.get('artifacts_sha256')).items():assert sha(manifest.parent/name)==digest,(row['probe'],name)
assert (full,partial,unexecuted)==(13,1,1)
robust=read(HERE/'protected_master_audit.json')
assert all(r['fully_reproduces_raw_claim'] and not r['counterexample_to_certified_claim'] for r in robust['cases'])
print('VERIFIED FINAL CLOSEOUT:13 full seals,1 conservative reference proof with original master rejected,1 not executed; no active owned jobs; automation PAUSED at closeout. Stability and660s target remain unresolved.')
