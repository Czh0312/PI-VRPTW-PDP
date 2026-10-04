"""Check frozen evidence and save a diagnostic decision, never adoption."""
from pathlib import Path
import collections,gzip,hashlib,json,os
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,indent=2),encoding='utf-8')
def hashes(base):return {str(p.relative_to(base)):sha(p) for p in sorted(base.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
checks={}
for base,manifest in [('frozen_source','source_manifest.json'),('benchmark_inputs','input_hashes.json'),('incumbents','incumbent_hashes.json')]:
    checks[base]=hashes(OUT/base)==json.loads((OUT/manifest).read_text())
for name in ('registration_hashes.json','expanded_registration_hashes.json','queue_registration_hashes.json'):
    checks[name]=all(sha(OUT/f)==h for f,h in json.loads((OUT/name).read_text()).items())
owned=json.loads((OUT/'live_owned_hashes.json').read_text())
checks['live_owned_unchanged']=all(sha(ROOT/f)==h for f,h in owned.items())
checks['alns_context_unchanged']=all(sha(ROOT/f)==h for f,h in json.loads((OUT/'source_manifest.json').read_text()).items() if f.startswith('models/routing/solvers/alns/'))
origin_checks=[]
for name,origin in json.loads((OUT/'input_origins.json').read_text()).items():
    if not origin['path_only']:origin_checks.append(sha(OUT/name)==origin['sha256']);continue
    before=json.loads(Path(origin['source']).read_text());after=json.loads((OUT/name).read_text())
    prev=OUT.parent/'alns_single_batch_late_pickup_confirm_20261002'
    expected={k:v.replace(str(prev/'benchmark_inputs'),str(OUT/'benchmark_inputs')).replace(str(prev/'before_source'),str(OUT/'frozen_source')) if isinstance(v,str) else v for k,v in before.items()}
    origin_checks.append(after==expected)
checks['business_inputs_unchanged']=all(origin_checks)
checks['incumbent_origins_unchanged']=all(sha(Path(r['origin']))==r['sha256']==sha(OUT/'incumbents'/r['name']) for r in json.loads((OUT/'incumbent_manifest.json').read_text()))
main=json.loads((OUT/'summary.json').read_text());expanded=json.loads((OUT/'expanded_summary.json').read_text());queue=json.loads((OUT/'queue_summary.json').read_text())
checks['all16_reconstructed_valid_equal']=len(main)==16 and all(r['reconstruction_valid'] and r['reconstruction_objective_equal'] for r in main)
checks['joint_trials_complete']=all(len(list((OUT/'diagnostic'/Path(r['name']).stem).glob('trial_*.json')))==r['attempts'] for r in main)
checks['no_joint_improvement']=all(r['best'] is None and not r['barriers'] for r in main)
checks['all_expanded_complete']=len(expanded)==16 and all(not r.get('enumeration_timeout',True) and not r.get('time_limit_reached',True) and r['enumerated']==r['inspected'] for r in expanded)
checks['expanded_trials_complete']=all(len(list((OUT/'expanded'/Path(r['name']).stem).glob('rank_*.json.gz')))==r['decoded'] for r in expanded)
checks['all32_queues_exhausted']=len(queue)==16 and all(len(r['queues'])==2 and all(q['exhausted'] for q in r['queues']) for r in queue)
checks['queue_trials_complete']=all(len(list((OUT/'queues'/Path(r['name']).stem).glob('cap%d_trial*.json.gz'%q['capacity'])))==q['proposed'] for r in queue for q in r['queues'])
checks['both_setup_failures_retained']=all((OUT/folder/'failure.json').exists() for folder in ('queue_setup_failure','queue_setup_failure_2'))
checks['nine_correctness_checks']= 'Ran 7 tests' in (OUT/'correctness_tests.log').read_text() and '\nOK\n' in (OUT/'correctness_tests.log').read_text() and 'Ran 2 tests' in (OUT/'queue_correctness_tests.log').read_text() and '\nOK\n' in (OUT/'queue_correctness_tests.log').read_text()
mode=collections.Counter();valid=collections.Counter();issues=collections.Counter()
for r in main:
    for row in json.loads((OUT/'diagnostic'/Path(r['name']).stem/'trials_summary.json').read_text()):
        mode[row['mode']]+=1;valid[row['mode']]+=row['valid']
        if row['mode']=='e2_only':issues.update(set(row['issues']))
strata=[dict(limit=limit,screen_passed=sum(r['strata'][i]['screen_passed'] for r in expanded),
             valid=sum(r['strata'][i]['valid'] for r in expanded),improving=sum(r['strata'][i]['improving'] for r in expanded)) for i,limit in enumerate((64,256,4096))]
queue_wins={cap:sum(q['best'] is not None for r in queue for q in r['queues'] if q['capacity']==cap) for cap in (256,1024)}
checks['queue_results_match_expanded']=queue_wins=={256:0,1024:10} and all(next(q for q in r['queues'] if q['capacity']==1024)['best']==None if e['best'] is None else abs(next(q for q in r['queues'] if q['capacity']==1024)['best']['objective']-e['best']['objective'])<1e-7 for r,e in zip(queue,expanded))
# Validate every reported best artifact against its stored full audit.
best_checks=[]
for row in queue:
    for q in row['queues']:
        if q['best'] is None:continue
        path=OUT/'queues'/Path(row['name']).stem/('cap%d_trial%04d.json.gz'%(q['capacity'],q['best']['index']))
        with gzip.open(path,'rt',encoding='utf-8') as f:raw=json.load(f)
        best_checks.append(raw['independent_audit']['valid'] and raw['independent_audit']['periodic_replay']['valid'] and not raw['measure']['measure']['issues'] and abs(raw['result']['info']['objective']-q['best']['objective'])<1e-7)
checks['all10_best_full_audit']=len(best_checks)==10 and all(best_checks)
summary=dict(joint_trials=sum(r['attempts'] for r in main),joint_modes=dict(mode),joint_valid_by_mode=dict(valid),
    top64_e2_violations=dict(issues),joint_improving_incumbents=0,expanded_geometry_total=sum(r['enumerated'] for r in expanded),
    expanded_decoded=sum(r['decoded'] for r in expanded),expanded_best_incumbents=sum(r['best'] is not None for r in expanded),
    expanded_rank_strata=strata,queue_proposals=sum(q['proposed'] for r in queue for q in r['queues']),queue_best_incumbents=queue_wins,
    additional_best_range_percent=[min(r['best']['change_percent'] for r in expanded if r['best']),max(r['best']['change_percent'] for r in expanded if r['best'])],
    evidence_limit='Extra held-incumbent computation,16 correlated solutions of3families; no equal-time solver quality/speed/optimality/GAP claim',load=os.getloadavg())
write(OUT/'analysis.json',summary)
checks['all_passed']=all(checks.values());write(OUT/'final_verification.json',checks);print(json.dumps(dict(checks=checks,analysis=summary),indent=2));assert checks['all_passed']
write(OUT/'decision.json',dict(decision='DIAGNOSTIC_ONLY_NO_PRODUCTION_CHANGE',joint_repair='No improvements within registered bounded trials; retain negative result',
    next_candidate='M3-only descent queue capacity256to1024, existing filters/full audits preserved; needs fresh correctness +50/100 short/long serial equal-time quality registration',
    accepted_late_pickup_preserved=True,verification='PASSED',evidence_limit=summary['evidence_limit']))
write(OUT/'raw_result_hashes.json',{str(p.relative_to(OUT)):sha(p) for base in ('diagnostic','expanded','queues') for p in sorted((OUT/base).rglob('*')) if p.is_file()})
