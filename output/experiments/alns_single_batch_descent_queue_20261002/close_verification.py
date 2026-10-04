"""Verify registered result, active source, and the preserved archive chain."""
from pathlib import Path
import hashlib,json,re
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2]
OWN='models/routing/solvers/alns/single_batch.py';TEST='tests/routing/test_single_batch_descent_queue.py'
CONFIRM=OUT.parent/'alns_single_batch_late_pickup_confirm_20261002'
PILOT=OUT.parent/'alns_single_batch_late_pickup_20261002'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def hashes(base):return {str(p.relative_to(base)):sha(p) for p in sorted(base.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
def frozen(folder):
    checks={}
    for source in ('before_source','after_source','disabled_source','adopted_source','restored_source'):
        path=folder/(source+'_manifest.json')
        if path.exists():checks[source]=hashes(folder/source)==json.loads(path.read_text())
    path=folder/'input_hashes_completed.json'
    if not path.exists():path=folder/'input_hashes.json'
    checks['inputs']=hashes(folder/'benchmark_inputs')==json.loads(path.read_text())
    path=folder/'registration_hashes.json'
    if path.exists():checks['registration']=all(sha(folder/f)==h for f,h in json.loads(path.read_text()).items())
    return checks
decision=json.loads((OUT/'decision.json').read_text());rejected=decision['reversal_required']
checks={'current':frozen(OUT)}
origins=json.loads((OUT/'input_origins.json').read_text());orig=[]
for name,origin in origins.items():
    if not origin['path_only']:orig.append(sha(OUT/name)==origin['sha256']);continue
    before=json.loads(Path(origin['source']).read_text());after=json.loads((OUT/name).read_text())
    path_keys={'data','orders','routing_config','pool_config','container_pool_config','dataset_metadata'}
    orig.append(all(v==before[k] or (k in path_keys and isinstance(v,str) and Path(v).is_file() and str(Path(v).resolve()).startswith(str(OUT))) for k,v in after.items()) and set(before)==set(after))
checks['origins_preserved']=all(orig)
checks['one_source_difference']=json.loads((OUT/'source_scope.json').read_text())['source_differences']==[OWN]
checks['preflight']=all(r['passed'] for r in json.loads((OUT/'input_preflight.json').read_text()))
disabled=json.loads((OUT/'disabled_equivalence.json').read_text())
checks['two_disabled_exact_audited']=len(disabled)==2 and all(r['exact'] and r['valid'] for r in disabled)
rows=json.loads((OUT/'quality/summary.json').read_text())
checks['all16_run_statuses_saved']=len(rows)==16 and all((OUT/'quality'/('%03d_%s.log'%(r['index'],r['source']))).is_file() for r in rows)
checks['quality_audit_gate_consistent']=json.loads((OUT/'quality_comparison.json').read_text())['all16_valid']==all(r.get('valid') and r.get('periodic') for r in rows)
checks['decision_matches_gates']=rejected==(not(decision['quality_gate_passed'] and decision['mechanism_gate_passed']))
expected=json.loads((OUT/'owned_hashes.json').read_text())
if rejected:
    checks['only_owned_module_restored']=sha(ROOT/OWN)==sha(OUT/'before_source'/OWN)
    checks['restored_snapshot_exact']=json.loads((OUT/'before_source_manifest.json').read_text())==json.loads((OUT/'restored_source_manifest.json').read_text())
    checks['new_test_archived']=not(ROOT/TEST).exists() and sha(OUT/'rejected_tests'/Path(TEST).name)==expected[TEST]
else:
    checks['candidate_owned_preserved']=all(sha(ROOT/f)==h for f,h in expected.items())
    checks['adopted_snapshot_exact']=json.loads((OUT/'after_source_manifest.json').read_text())==json.loads((OUT/'adopted_source_manifest.json').read_text())
accepted=json.loads((OUT/'accepted_owned_hashes.json').read_text())
checks['accepted_late_pickup_test_preserved']=sha(ROOT/'tests/routing/test_single_batch_late_pickup.py')==accepted['tests/routing/test_single_batch_late_pickup.py']
checks['accepted_late_pickup_source_is_control']=sha(OUT/'before_source'/OWN)==accepted[OWN]
live=json.loads((OUT/'live_equivalence.json').read_text())
checks['all8_public_groups']=len(live)==8 and all(r['identical'] and r['valid'] if rejected else r['candidate_live_exact'] and r['valid'] and (r['model']==3 or r['baseline_equal']) for r in live)
regression=json.loads((OUT/'regression_result.json').read_text());summary=re.search(r'\d+ passed(?:, \d+ warnings)? in [0-9.]+s',(OUT/'full_regression.log').read_text())
checks['fresh_final_full_regression']=regression['exit_code']==0 and summary is not None
chain={sha(OUT/'before_source'/OWN),sha(OUT/'after_source'/OWN),sha(PILOT/'before_source'/OWN)}
archives=[('late_confirm',CONFIRM),('late_pilot',PILOT),('e1_trials',OUT.parent/'alns_single_batch_e1_trials_20261002'),('vehicle_seed',OUT.parent/'alns_single_batch_vehicle_seed_20261002')]
archives += [(name,OUT.parent/('alns_single_batch_'+name+'_20261001')) for name in ('audited_tail','terminal_tail','lazy_pickup','route_recombine','compact_repair','route_group','improving_groups','delivery_prefix','event_capacity','direct_graph','direct_relax')]
for label,folder in archives:
    c=frozen(folder)
    path=folder/'restored_owned_hashes.json';restored=path.exists()
    if not restored:path=folder/'owned_hashes.json'
    if not path.exists():path=folder/'live_owned_hashes.json'
    old=json.loads(path.read_text())
    c['other_live_owned']=all(sha(ROOT/f)==h for f,h in old.items() if f!=OWN)
    if OWN in old:c['module_history_preserved']=old[OWN] in chain
    if restored:c['archived_tests']=all(sha(folder/'rejected_tests'/Path(f).name)==h for f,h in json.loads((folder/'owned_hashes.json').read_text()).items() if f.startswith('tests/'))
    checks[label]=c
joint=OUT.parent/'alns_single_batch_joint_supply_diagnostic_20261002'
checks['joint_diagnostic_archive']={
    'source':hashes(joint/'frozen_source')==json.loads((joint/'source_manifest.json').read_text()),
    'inputs':hashes(joint/'benchmark_inputs')==json.loads((joint/'input_hashes.json').read_text()),
    'incumbents':hashes(joint/'incumbents')==json.loads((joint/'incumbent_hashes.json').read_text()),
    'closure':all(sha(joint/f)==h for f,h in json.loads((joint/'closure_hashes.json').read_text()).items()),
    'all_raw_paths_present':all((joint/f).is_file() for f in json.loads((joint/'raw_result_hashes.json').read_text()))}
def valid(value):return all(valid(v) for v in value.values()) if isinstance(value,dict) else value is True
checks['all_passed']=valid(checks);(OUT/'final_verification.json').write_text(json.dumps(checks,indent=2));print(json.dumps(checks,indent=2));assert checks['all_passed']
decision.update(verification='PASSED',regression_summary=summary.group(0),prior_archives_preserved=16)
(OUT/'decision.json').write_text(json.dumps(decision,indent=2))
