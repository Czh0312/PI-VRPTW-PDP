"""Close registered confirmation, preserving both pilot and negative evidence."""
from pathlib import Path
import hashlib,json,re
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
PILOT=OUT.parent/'alns_single_batch_late_pickup_20261002'
OWN='models/routing/solvers/alns/single_batch.py'
TEST='tests/routing/test_single_batch_late_pickup.py'
decision=json.loads((OUT/'decision.json').read_text())
withdraw=decision['reversal_required']
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def hashes(base):
    return {str(p.relative_to(base)):sha(p) for p in sorted(base.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts}
def frozen(folder):
    c={}
    for source in ('before_source','after_source','disabled_source','adopted_source','restored_source'):
        m=folder/(source+'_manifest.json')
        if m.exists(): c[source]=hashes(folder/source)==json.loads(m.read_text())
    m=folder/'input_hashes_completed.json'
    if not m.exists(): m=folder/'input_hashes.json'
    c['inputs']=hashes(folder/'benchmark_inputs')==json.loads(m.read_text())
    r=folder/'registration_hashes.json'
    if r.exists(): c['registration']=all(sha(folder/f)==h for f,h in json.loads(r.read_text()).items())
    return c
checks={'confirmation':frozen(OUT)}
checks['origins_preserved']=all(sha(OUT/f)==r['sha256']
    for f,r in json.loads((OUT/'input_origins.json').read_text()).items())
checks['one_source_difference']=json.loads((OUT/'source_scope.json').read_text())['source_differences']==[OWN]
checks['input_preflight']=all(r['passed'] for r in json.loads((OUT/'input_preflight.json').read_text()))
rows=json.loads((OUT/'quality/summary.json').read_text())
checks['all16_independent_audits']=len(rows)==16 and all(r['valid'] and r['periodic'] for r in rows)
disabled=json.loads((OUT/'disabled_equivalence.json').read_text())
checks['disabled_exact']=len(disabled)==2 and all(r['exact'] and r['valid'] for r in disabled)
checks['decision_matches_gates']=withdraw == (not (decision['quality_gate_passed'] and decision['mechanism_gate_passed']))
chain={sha(OUT/'before_source'/OWN),sha(OUT/'after_source'/OWN)}
archives=[('pilot',PILOT),('e1_trials',OUT.parent/'alns_single_batch_e1_trials_20261002'),
          ('vehicle_seed',OUT.parent/'alns_single_batch_vehicle_seed_20261002')]
archives += [(n,OUT.parent/('alns_single_batch_'+n+'_20261001')) for n in
             ('audited_tail','terminal_tail','lazy_pickup','route_recombine','compact_repair','route_group',
              'improving_groups','delivery_prefix','event_capacity','direct_graph','direct_relax')]
for label,folder in archives:
    c=frozen(folder)
    m=folder/'restored_owned_hashes.json'
    restored=m.exists()
    if not restored:m=folder/'owned_hashes.json'
    old=json.loads(m.read_text())
    c['nonoverlap_live_owned']=all(sha(ROOT/f)==h for f,h in old.items() if f!=OWN)
    if OWN in old:c['overlap_history_preserved']=old[OWN] in chain
    if restored:
        c['archived_tests']=all(sha(folder/'rejected_tests'/Path(f).name)==h
            for f,h in json.loads((folder/'owned_hashes.json').read_text()).items() if f.startswith('tests/'))
    checks[label]=c
live=json.loads((OUT/'live_equivalence.json').read_text())
if withdraw:
    checks['owned_restored']=sha(ROOT/OWN)==sha(OUT/'before_source'/OWN)
    checks['test_archived']=not (ROOT/TEST).exists() and sha(PILOT/'rejected_tests'/Path(TEST).name)==json.loads((PILOT/'owned_hashes.json').read_text())[TEST]
    checks['restored_snapshot_exact']=json.loads((OUT/'before_source_manifest.json').read_text())==json.loads((OUT/'restored_source_manifest.json').read_text())
    checks['all8_public_groups']=len(live)==8 and all(r['identical'] and r['valid'] for r in live)
    regression=json.loads((OUT/'regression_result.json').read_text())
    text=(OUT/'full_regression.log').read_text()
    summary=re.search(r'\d+ passed(?:, \d+ warnings)? in [0-9.]+s',text)
    checks['fresh_restored_regression']=regression['exit_code']==0 and summary is not None
else:
    expected=json.loads((OUT/'live_owned_hashes.json').read_text())
    checks['owned_unchanged']=all(sha(ROOT/f)==h for f,h in expected.items())
    checks['all8_public_groups']=len(live)==8 and all(r['candidate_live_exact'] and r['valid'] and
                                                  (r['model']==3 or r['baseline_equal']) for r in live)
    checks['unchanged_code_prior_full_regression']=json.loads((PILOT/'regression_result.json').read_text())['exit_code']==0
def valid(v): return all(valid(x) for x in v.values()) if isinstance(v,dict) else v is True
checks['all_passed']=valid(checks)
(OUT/'final_verification.json').write_text(json.dumps(checks,indent=2));print(json.dumps(checks,indent=2))
assert checks['all_passed']
decision.update(decision='WITHDRAWN_AFTER_REGISTERED_LONG_CONFIRMATION' if withdraw else 'RETAINED_AFTER_REGISTERED_LONG_CONFIRMATION',
                verification='PASSED',prior_pilot_results_preserved=True,
                evidence_limit='Finite30/60s pairedmatrix; no universal/hourscale quality, causality, optimality or trueGAP claim')
if withdraw:decision['regression_summary']=summary.group(0)
(OUT/'decision.json').write_text(json.dumps(decision,indent=2))
