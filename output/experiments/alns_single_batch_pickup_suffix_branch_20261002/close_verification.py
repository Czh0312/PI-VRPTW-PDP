"""Close a failed equivalent computation trial, preserving its prior evidence chain."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib,json,os
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2]
PRIOR=OUT.parent/'alns_single_batch_descent_load_screen_confirm_20261002'
PROFILE=OUT.parent/'alns_single_batch_route_path_cache_20261002'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def verify(base,manifest):return all((base/f).is_file() and sha(base/f)==h for f,h in read(manifest).items())
checks={}
for name in ('before_source','after_source','disabled_source','restored_source'):
 checks[name]=verify(OUT/name,OUT/(name+'_manifest.json'))
checks['inputs']=verify(OUT/'benchmark_inputs',OUT/'input_hashes.json')
checks['registration']=verify(OUT,OUT/'registration_hashes.json')
preflight=read(OUT/'input_preflight.json');checks['input_preflight']=len(preflight)==8 and all(r['passed'] and r['precheck_passed'] and r['all_paths_closed'] for r in preflight)
checks['root_owned_restored']=verify(ROOT,OUT/'restored_owned_hashes.json')
checks['candidate_snapshot_preserved']=verify(OUT/'candidate_owned',OUT/'candidate_owned_hashes.json')
owned=read(OUT/'candidate_owned_hashes.json');test='tests/routing/test_single_batch_pickup_suffix_branch.py'
checks['archived_test']=sha(OUT/'rejected_tests'/test)==owned[test] and not (ROOT/test).exists()
a,b=read(OUT/'before_source_manifest.json'),read(OUT/'after_source_manifest.json')
checks['only_owned_source_changed']=[f for f,h in a.items() if b[f]!=h]==['models/routing/solvers/alns/single_batch_scoring.py']
checks['disabled_exact']=len(read(OUT/'disabled_equivalence.json'))==2 and all(r['exact'] and r['valid'] for r in read(OUT/'disabled_equivalence.json'))
cpu=read(OUT/'primary_decision.json');checks['all12_cpu_exact_audited']=cpu['runs']==12 and all(r['identical'] and r['all_valid'] for r in cpu['groups'])
checks['cpu_rejection_rule']=not cpu['eligible'] and all(r['cpu_reduction_percent']<2 for r in cpu['groups'])
checks['no_quality_started']=not (OUT/'quality').exists()
checks['fresh_restored_full_regression']='2989 passed, 14 warnings in 71.71s' in (OUT/'full_regression.log').read_text()
public=read(OUT/'live_equivalence.json');checks['restored_public8groups24solves']=len(public)==8 and all(r['identical'] and r['valid'] and r['source']=='restored_source' for r in public)
checks['previous_closure_bytes']=verify(PRIOR,PRIOR/'closure_hashes.json')
checks['previous_raw_bytes']=verify(PRIOR,PRIOR/'raw_result_hashes.json')
checks['previous_verification_passed']=read(PRIOR/'decision.json')['verification']=='PASSED'
checks['profile_source']=verify(PROFILE/'frozen_source',PROFILE/'source_manifest.json') if (PROFILE/'source_manifest.json').exists() else verify(PROFILE/'before_source',PROFILE/'before_source_manifest.json')
checks['profile_input']=verify(PROFILE/'benchmark_inputs',PROFILE/'input_hashes.json')
checks['profile_registration']=verify(PROFILE,PROFILE/'diagnostic_registration_hashes.json')
checks['profile_raw']=verify(PROFILE,PROFILE/'raw_result_hashes.json')
checks['profile_valid']=read(PROFILE/'decision.json')['verification']=='PASSED'
result=dict(checks=checks,all_passed=all(checks.values()),predecessor_verified_archives=19,previous_closure_files=len(read(PRIOR/'closure_hashes.json')),previous_raw_files=len(read(PRIOR/'raw_result_hashes.json')),profile_raw_files=len(read(PROFILE/'raw_result_hashes.json')),source_files=len(a),input_files=len(read(OUT/'input_hashes.json')),verification='Direct predecessor closure+raw SHA preservation, not fresh rerun of all historical experiments or 2.2GB joint diagnostic.',updated=datetime.now(timezone.utc).isoformat(),load=os.getloadavg(),shared_source_drift=[f for f,h in a.items() if (ROOT/f).is_file() and sha(ROOT/f)!=h])
(OUT/'final_verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));assert result['all_passed']
